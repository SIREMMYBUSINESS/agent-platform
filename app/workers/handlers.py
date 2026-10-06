"""Built-in queue handlers; each delegates business decisions to its service."""

from datetime import UTC, datetime
from uuid import UUID

from app.config import settings
from app.persistence.session import session_factory
from app.persistence.unit_of_work import UnitOfWork
from app.queue.runtime import QueueTaskEnvelope, TaskHandlerRegistry
from app.workers.event_replay import PersistedEvent, subscribers
from app.schemas import InboundRequest
from app.security import AuthenticatedPrincipal
from app.services.campaign_analytics_service import CampaignAnalyticsService
from app.services.intake_service import IntakeService
from app.services.outreach_execution import OutreachExecutionService
from app.services.scheduling_service import SchedulingService
from app.workflows.escalation import EscalationService
from app.workflows.receptionist import ReceptionistActions, ReceptionistWorkflowEngine


def _trusted_worker(task: QueueTaskEnvelope, *roles: str) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        principal_id=f"worker:{task.id}", tenant_id=task.tenant_id,
        roles=tuple(roles), auth_source="internal_worker", issued_at=datetime.now(UTC),
    )


def build_default_handlers(*, message_sender=None) -> TaskHandlerRegistry:
    handlers = TaskHandlerRegistry()

    def replay_event(task: QueueTaskEnvelope) -> dict:
        event_id = UUID(str(task.payload["source_event_id"]))
        with UnitOfWork(session_factory) as uow:
            event = uow.events.get_by_id(
                tenant_id=task.tenant_id, business_id=task.business_id,
                event_id=event_id,
            )
            if event is None:
                raise LookupError("event is not available in trusted scope")
            snapshot = PersistedEvent(
                id=event.id, tenant_id=event.tenant_id, business_id=event.business_id,
                conversation_id=event.conversation_id, event_type=event.event_type,
                payload=dict(event.payload), source=event.source,
                correlation_id=event.correlation_id, created_at=event.created_at,
            )
        output = subscribers.get(snapshot.event_type)(snapshot, task) or {}
        if not isinstance(output, dict):
            raise TypeError("event subscriber must return an object")
        return output

    def inbound(task: QueueTaskEnvelope) -> dict:
        if task.task_name != "inbound.process":
            raise LookupError("unsupported inbound task")
        allowed = {key: task.payload[key] for key in ("contact_id", "channel", "text", "contact_name")
                   if key in task.payload}
        request = InboundRequest(
            tenant_id=task.tenant_id, business_id=task.business_id, **allowed,
        )
        return IntakeService(
            trusted_tenant_id=task.tenant_id,
            uow_factory=lambda: UnitOfWork(session_factory),
        ).handle(request)

    def outreach(task: QueueTaskEnvelope) -> dict:
        if task.task_name != "outreach.execute_send":
            raise LookupError("unsupported outreach task")
        message_id = UUID(str(task.payload["message_id"]))
        service = OutreachExecutionService(
            uow_factory=lambda: UnitOfWork(session_factory),
            sender=message_sender,
        )
        principal = _trusted_worker(task, "campaign_writer")
        if task.attempts > 1:
            service.retry_send(principal, message_id)
        result = service.execute_send(principal, message_id)
        if result.status == "failed":
            raise RuntimeError("provider_delivery_failed")
        return {"message_id": str(result.id), "status": result.status}

    def escalation(task: QueueTaskEnvelope) -> dict:
        if task.task_name != "escalation.sweep":
            raise LookupError("unsupported escalation task")
        engine = ReceptionistWorkflowEngine(
            uow_factory=lambda: UnitOfWork(session_factory),
        )
        actions = ReceptionistActions(engine)
        service = EscalationService(
            uow_factory=lambda: UnitOfWork(session_factory), actions=actions,
        )
        results = service.run_due(
            _trusted_worker(task, "workflow_executor"), business_id=task.business_id,
        )
        return {"escalated": len(results)}

    def scheduling(task: QueueTaskEnvelope) -> dict:
        if task.task_name != "scheduling.confirm_appointment":
            raise LookupError("unsupported scheduling task")
        conversation_id = UUID(str(task.payload["conversation_id"]))
        with UnitOfWork(session_factory) as uow:
            conversation = uow.conversations.get_for_update(
                tenant_id=task.tenant_id, conversation_id=conversation_id,
            )
            if conversation is None or conversation.business_id != task.business_id:
                raise LookupError("appointment conversation is unavailable")
            contact = uow.contacts.get(
                tenant_id=task.tenant_id, contact_id=conversation.contact_id,
            )
            if contact is None or contact.business_id != task.business_id:
                raise LookupError("appointment contact is unavailable")
            result = SchedulingService().confirm(
                uow=uow, tenant_id=task.tenant_id, business_id=task.business_id,
                contact_id=contact.id, contact_timezone=contact.timezone,
            )
            if result is None:
                return {"status": "no_proposed_appointment"}
            uow.workflows.append(
                tenant_id=task.tenant_id, conversation_id=conversation.id,
                state="appointment_confirmed",
                context={"appointment_id": str(result.appointment.id)},
            )
            uow.events.add(
                tenant_id=task.tenant_id, business_id=task.business_id,
                conversation_id=conversation.id, event_type="appointment.confirmed",
                payload={"appointment_id": str(result.appointment.id)},
                source="scheduling_worker", correlation_id=task.correlation_id,
            )
            return {"appointment_id": str(result.appointment.id), "status": "confirmed"}

    def analytics(task: QueueTaskEnvelope) -> dict:
        if task.task_name != "analytics.refresh_campaign":
            raise LookupError("unsupported analytics task")
        result = CampaignAnalyticsService(
            trusted_tenant_id=task.tenant_id, trusted_business_id=task.business_id,
            uow_factory=lambda: UnitOfWork(session_factory),
        ).get(UUID(str(task.payload["campaign_id"])))
        return result

    handlers.register("inbound", "inbound.process", inbound)
    handlers.register("outreach", "outreach.execute_send", outreach)
    handlers.register("scheduling", "scheduling.confirm_appointment", scheduling)
    handlers.register("escalation", "escalation.sweep", escalation)
    handlers.register("analytics", "analytics.refresh_campaign", analytics)
    for domain in ("inbound", "outreach", "scheduling", "escalation", "analytics"):
        handlers.register(domain, "events.replay", replay_event)
    # Scheduling handlers are registered by deployments with their calendar
    # and reminder adapters; unconfigured work follows normal retry/DLQ policy.
    return handlers


__all__ = ["build_default_handlers"]
