"""Built-in queue handlers; each delegates business decisions to its service."""

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

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
from app.services.enrollment_service import EnrollmentService
from app.services.policy_service import PolicyService
from app.services.scheduling_service import SchedulingService
from app.services.appointment_reminder_service import AppointmentReminderService
from app.services.follow_up_service import FollowUpService
from app.services.outbound_delivery_service import OutboundDeliveryService
from app.services.escalation_service import EscalationService as EscalationTicketService
from app.services.faq_service import FAQService
from app.services.reply_service import ReplyService
from app.schemas import CampaignReplyRequest, CampaignTriggerRequest
from app.integrations.crm.events import CRMEventType, CRMEvent
from app.integrations.crm.sync_engine import CRMSyncEngine
from app.workflows.escalation import EscalationService
from app.workflows.receptionist import ReceptionistActions, ReceptionistWorkflowEngine
from app.optimization.feedback import FeedbackEventCapture
from app.optimization.segments import segment_for_lead
from app.regions.service import RegionService


def _trusted_worker(task: QueueTaskEnvelope, *roles: str) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        principal_id=f"worker:{task.id}", tenant_id=task.tenant_id,
        roles=tuple(roles), auth_source="internal_worker", issued_at=datetime.now(UTC),
    )


def _authorize_provider_region(task: QueueTaskEnvelope, provider_kind: str,
                               resource_type: str, resource_id: str) -> str | None:
    with UnitOfWork(session_factory) as uow:
        business = uow.businesses.get(tenant_id=task.tenant_id,
                                      business_id=task.business_id)
        if business is None:
            raise LookupError("regional business scope is unavailable")
        return RegionService(uow_factory=lambda: UnitOfWork(session_factory)).authorize_provider_transfer(
            uow=uow, tenant_id=task.tenant_id, business=business,
            provider_kind=provider_kind, resource_type=resource_type,
            resource_id=resource_id, actor_id=f"worker:{task.id}",
        )


def build_default_handlers(*, message_sender=None, calendar_provider=None, crm_provider=None,
                           faq_service: FAQService | None = None) -> TaskHandlerRegistry:
    handlers = TaskHandlerRegistry()

    def mark_campaign_lead_contacted(uow, task, message) -> None:
        run_id = task.payload.get("campaign_step_run_id")
        if message.delivery_status != "sent" or not run_id:
            return
        run = uow.campaign_step_runs.get(
            tenant_id=task.tenant_id, business_id=task.business_id,
            run_id=UUID(str(run_id)),
        )
        enrollment = (uow.campaign_enrollments.get_by_id(
            tenant_id=task.tenant_id, business_id=task.business_id,
            enrollment_id=run.enrollment_id,
        ) if run else None)
        lead = (uow.leads.get(
            tenant_id=task.tenant_id, business_id=task.business_id,
            lead_id=enrollment.lead_id,
        ) if enrollment else None)
        if lead is not None and lead.status == "qualified":
            uow.lead_transitions.transition(
                tenant_id=task.tenant_id, business_id=task.business_id,
                lead_id=lead.id, expected_state="qualified", new_state="contacted",
                context={"campaign_id": str(enrollment.campaign_id),
                         "campaign_step_run_id": str(run.id)},
            )
            uow.campaign_events.add(
                tenant_id=task.tenant_id, business_id=task.business_id,
                campaign_id=enrollment.campaign_id, enrollment_id=enrollment.id,
                event_type="lead.contacted",
                payload={"lead_id": str(lead.id), "message_id": str(message.id),
                         "step_order": run.step_order},
            )

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
        enrollment = campaign = None
        external_message_id = task.payload.get("external_message_id")
        if request.channel in {"sms", "email"} and external_message_id:
            with UnitOfWork(session_factory) as uow:
                enrollment = uow.campaign_enrollments.latest_active_for_contact(
                    tenant_id=task.tenant_id, business_id=task.business_id,
                    contact_id=request.contact_id,
                )
                if enrollment is not None:
                    campaign = uow.campaigns.get(
                        tenant_id=task.tenant_id, business_id=task.business_id,
                        campaign_id=enrollment.campaign_id,
                    )
                    if campaign is None or campaign.channel != request.channel:
                        enrollment = campaign = None
        result = IntakeService(
            trusted_tenant_id=task.tenant_id,
            uow_factory=lambda: UnitOfWork(session_factory),
            calendar_provider=calendar_provider,
            faq_service=faq_service,
            public_app_url=settings.public_app_url,
        ).handle(request, correlation_id=task.correlation_id,
                 receptionist_enabled=enrollment is None)
        if not result.get("success") or enrollment is None or campaign is None:
            return result
        reply = ReplyService(
            trusted_tenant_id=task.tenant_id, trusted_business_id=task.business_id,
            uow_factory=lambda: UnitOfWork(session_factory),
        ).process(campaign.id, CampaignReplyRequest(
            tenant_id=task.tenant_id, enrollment_id=enrollment.id,
            text=request.text[:4000], external_message_id=str(external_message_id),
        ), correlation_id=task.correlation_id)
        return {**result, "campaign_reply": {
            "classification": reply["classification"], "duplicate": reply["duplicate"],
        }}

    def outreach(task: QueueTaskEnvelope) -> dict:
        if task.task_name == "outreach.trigger_enrollment":
            result = EnrollmentService(
                trusted_tenant_id=task.tenant_id,
                trusted_business_id=task.business_id,
                uow_factory=lambda: UnitOfWork(session_factory),
            ).trigger(UUID(str(task.payload["campaign_id"])), CampaignTriggerRequest(
                tenant_id=task.tenant_id,
                enrollment_id=UUID(str(task.payload["enrollment_id"])),
            ))
            return result
        if task.task_name == "communication.deliver_message":
            message_id = UUID(str(task.payload["message_id"]))
            with UnitOfWork(session_factory) as uow:
                message = uow.messages.get_in_tenant(
                    tenant_id=task.tenant_id, message_id=message_id,
                )
                if message is None or message.direction != "outbound":
                    raise LookupError("outbound message is unavailable")
                if message.delivery_status in {"sent", "blocked", "dry_run"}:
                    mark_campaign_lead_contacted(uow, task, message)
                    return {"message_id": str(message.id), "status": message.delivery_status,
                            "provider_message_id": message.provider_message_id}
                conversation = uow.conversations.get(
                    tenant_id=task.tenant_id, conversation_id=message.conversation_id,
                )
                contact = (uow.contacts.get(tenant_id=task.tenant_id, contact_id=conversation.contact_id)
                           if conversation else None)
                business = (uow.businesses.get(tenant_id=task.tenant_id, business_id=task.business_id)
                            if conversation else None)
                tenant = uow.tenants.get(tenant_id=task.tenant_id)
                if (conversation is None or contact is None or business is None or tenant is None
                        or conversation.business_id != task.business_id
                        or contact.business_id != task.business_id):
                    raise LookupError("outbound message scope is unavailable")
                decision = PolicyService().evaluate(
                    uow, tenant=tenant, business=business, contact=contact,
                    channel=message.channel, direction="outbound",
                )
                if not decision.allowed:
                    uow.messages.set_delivery(
                        tenant_id=task.tenant_id, message_id=message.id,
                        status="blocked", error_code=decision.reason_code,
                    )
                    if task.payload.get("campaign_step_run_id"):
                        uow.campaign_step_runs.set_status(
                            tenant_id=task.tenant_id, business_id=task.business_id,
                            run_id=UUID(str(task.payload["campaign_step_run_id"])),
                            status="blocked", skip_reason=decision.reason_code,
                        )
                    uow.events.add(
                        tenant_id=task.tenant_id, business_id=task.business_id,
                        conversation_id=conversation.id, event_type="outbound.delivery.blocked",
                        payload={"message_id": str(message.id), "reason_code": decision.reason_code},
                        source="message_delivery_worker", correlation_id=task.correlation_id,
                    )
                    return {"message_id": str(message.id), "status": "blocked"}
                if settings.workflow_dry_run:
                    uow.messages.set_delivery(
                        tenant_id=task.tenant_id, message_id=message.id, status="dry_run",
                    )
                    if task.payload.get("campaign_step_run_id"):
                        uow.campaign_step_runs.set_status(
                            tenant_id=task.tenant_id, business_id=task.business_id,
                            run_id=UUID(str(task.payload["campaign_step_run_id"])),
                            status="dry_run",
                        )
                    uow.events.add(
                        tenant_id=task.tenant_id, business_id=task.business_id,
                        conversation_id=conversation.id, event_type="outbound.delivery.dry_run",
                        payload={"message_id": str(message.id)}, source="message_delivery_worker",
                        correlation_id=task.correlation_id,
                    )
                    return {"message_id": str(message.id), "status": "dry_run"}
                channel, content = message.channel, message.content
                destination = (contact.email if channel == "email" else
                               contact.whatsapp if channel == "whatsapp" else contact.phone)
                subject = message.subject
                missing_destination = not destination
                if missing_destination:
                    uow.messages.set_delivery(
                        tenant_id=task.tenant_id, message_id=message.id,
                        status="failed", error_code="recipient_address_missing",
                    )
                    if task.payload.get("campaign_step_run_id") and task.attempts >= task.max_attempts:
                        uow.campaign_step_runs.set_status(
                            tenant_id=task.tenant_id, business_id=task.business_id,
                            run_id=UUID(str(task.payload["campaign_step_run_id"])),
                            status="failed", skip_reason="recipient_address_missing",
                        )
                stored_conversation_id = conversation.id
                stored_message_id = message.id
            if missing_destination:
                raise RuntimeError("recipient_address_missing")
            if message_sender is None:
                error_code, provider_id = "sender_not_configured", None
            else:
                try:
                    send_content = ({"subject": subject or "Message from your business",
                                     "body": content} if channel == "email" else {"text": content})
                    provider_region = _authorize_provider_region(
                        task, "email" if channel == "email" else "sms",
                        "message", str(stored_message_id),
                    )
                    provider_id = message_sender.send(
                        tenant_id=task.tenant_id, business_id=task.business_id,
                        channel=channel, recipient=destination, content=send_content,
                        idempotency_key=f"message:{stored_message_id}:delivery",
                        message_id=stored_message_id, attempt=task.attempts,
                        **({"region_id": provider_region} if provider_region else {}),
                    )
                    error_code = None
                except Exception as exc:
                    error_code, provider_id = f"provider_error:{type(exc).__name__}", None
            with UnitOfWork(session_factory) as uow:
                delivered_message = uow.messages.set_delivery(
                    tenant_id=task.tenant_id, message_id=stored_message_id,
                    status="failed" if error_code else "sent",
                    provider_message_id=provider_id, error_code=error_code,
                )
                if task.payload.get("campaign_step_run_id"):
                    step_run_id = UUID(str(task.payload["campaign_step_run_id"]))
                    uow.campaign_step_runs.set_status(
                        tenant_id=task.tenant_id, business_id=task.business_id,
                        run_id=step_run_id,
                        status=("failed" if error_code and task.attempts >= task.max_attempts
                                else "sent" if not error_code else "queued"),
                        sent_at=datetime.now(UTC) if not error_code else None,
                        skip_reason=error_code if error_code and task.attempts >= task.max_attempts else None,
                    )
                    if not error_code:
                        step_run = uow.campaign_step_runs.get(
                            tenant_id=task.tenant_id, business_id=task.business_id,
                            run_id=step_run_id,
                        )
                        enrollment = (uow.campaign_enrollments.get_by_id(
                            tenant_id=task.tenant_id, business_id=task.business_id,
                            enrollment_id=step_run.enrollment_id,
                        ) if step_run else None)
                        campaign = (uow.campaigns.get(
                            tenant_id=task.tenant_id, business_id=task.business_id,
                            campaign_id=enrollment.campaign_id,
                        ) if enrollment else None)
                        sequence = next((item for item in uow.campaign_sequences.list_for_campaign(
                            tenant_id=task.tenant_id, business_id=task.business_id,
                            campaign_id=enrollment.campaign_id,
                        ) if item.id == enrollment.sequence_id), None) if enrollment else None
                        FeedbackEventCapture().capture(
                            uow=uow, tenant_id=task.tenant_id, business_id=task.business_id,
                            event_key=f"campaign-step-sent:{step_run_id}",
                            correlation_id=task.correlation_id,
                            event_type="campaign_step_sent", outcome="sent",
                            lead_id=enrollment.lead_id if enrollment else None,
                            campaign_id=enrollment.campaign_id if enrollment else None,
                            decision={"variant": sequence.name if sequence else "unknown",
                                      "test_group": getattr(enrollment, "experiment_group", "all"),
                                      "step": step_run.step_order if step_run else -1,
                                      "sent_hour": datetime.now(UTC).hour,
                                      "segment": (segment_for_lead(
                                          uow, tenant_id=task.tenant_id,
                                          business_id=task.business_id,
                                          lead_id=enrollment.lead_id,
                                      ) if enrollment else "source=manual;industry=unknown;country=unknown")},
                            event_metadata={"channel": campaign.channel if campaign else channel},
                        )
                        FeedbackEventCapture().capture(
                            uow=uow, tenant_id=task.tenant_id, business_id=task.business_id,
                            event_key=f"action-taken:campaign-step:{step_run_id}",
                            correlation_id=task.correlation_id,
                            event_type="action_taken", outcome="sent",
                            lead_id=enrollment.lead_id if enrollment else None,
                            campaign_id=enrollment.campaign_id if enrollment else None,
                            decision={"action": ("send_email" if campaign and campaign.channel == "email"
                                                 else "send_whatsapp" if campaign and campaign.channel == "whatsapp"
                                                 else "send_sms"),
                                      "state": "new_lead"},
                            event_metadata={"variant": sequence.name if sequence else "unknown"},
                        )
                mark_campaign_lead_contacted(uow, task, delivered_message)
                uow.events.add(
                    tenant_id=task.tenant_id, business_id=task.business_id,
                    conversation_id=stored_conversation_id,
                    event_type="outbound.delivery.failed" if error_code else "outbound.delivery.sent",
                    payload={"message_id": str(stored_message_id),
                             **({"error_code": error_code} if error_code else {})},
                    source="message_delivery_worker", correlation_id=task.correlation_id,
                )
            if error_code:
                raise RuntimeError(error_code)
            return {"message_id": str(stored_message_id), "status": "sent",
                    "provider_message_id": provider_id}
        if task.task_name != "outreach.execute_send":
            raise LookupError("unsupported outreach task")
        message_id = UUID(str(task.payload["message_id"]))
        if settings.workflow_dry_run:
            with UnitOfWork(session_factory) as uow:
                message = uow.outreach_messages.get_for_tenant(
                    tenant_id=task.tenant_id, message_id=message_id,
                )
                if message is None or message.business_id != task.business_id:
                    raise LookupError("outreach message scope is unavailable")
                if message.status == "scheduled":
                    updated = uow.outreach_messages.set_state(
                        tenant_id=task.tenant_id, business_id=task.business_id,
                        message_id=message_id, expected_status="scheduled",
                        status="dry_run", at=datetime.now(UTC),
                    )
                    uow.campaign_events.add(
                        tenant_id=task.tenant_id, business_id=task.business_id,
                        campaign_id=message.campaign_id, event_type="message.dry_run",
                        payload={"message_id": str(message_id)},
                    )
                    return {"message_id": str(updated.id), "status": "dry_run"}
                if message.status == "dry_run":
                    return {"message_id": str(message_id), "status": "dry_run"}
                raise ValueError("outreach message is no longer eligible for dry-run")
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
        if task.task_name == "escalation.notify_staff":
            ticket_id = UUID(str(task.payload["ticket_id"]))
            with UnitOfWork(session_factory) as uow:
                ticket = uow.escalations.get_in_tenant(
                    tenant_id=task.tenant_id, ticket_id=ticket_id,
                )
                if ticket is None or ticket.business_id != task.business_id:
                    raise LookupError("escalation ticket is unavailable")
                staff = uow.staff.list_active(
                    tenant_id=task.tenant_id, business_id=task.business_id,
                )
                assigned = next((row for row in staff
                                 if row.id == getattr(ticket, "assigned_staff_id", None)), None)
                recipients = ([(assigned.id, assigned.email)] if assigned and assigned.email
                              else [(row.id, row.email) for row in staff if row.email])
                priority = ticket.priority
                conversation_id = ticket.conversation_id
            if not recipients:
                # The durable ticket and human.escalation.alert event remain
                # available in the authenticated dashboard inbox.
                return {"ticket_id": str(ticket_id), "status": "dashboard_inbox", "notified": 0}
            if settings.workflow_dry_run:
                with UnitOfWork(session_factory) as uow:
                    for staff_id, _ in recipients:
                        uow.provider_interactions.add(
                            tenant_id=task.tenant_id, business_id=task.business_id,
                            provider="email", operation="escalation_staff_notification",
                            idempotency_key=f"escalation:{ticket_id}:staff:{staff_id}:email",
                            outcome="dry_run",
                        )
                    uow.events.add(
                        tenant_id=task.tenant_id, business_id=task.business_id,
                        conversation_id=conversation_id,
                        event_type="human.escalation.notification.dry_run",
                        payload={"ticket_id": str(ticket_id), "recipient_count": len(recipients)},
                        source="escalation_notification_worker", correlation_id=task.correlation_id,
                    )
                return {"ticket_id": str(ticket_id), "status": "dry_run",
                        "notified": 0, "planned": len(recipients)}
            if message_sender is None:
                raise RuntimeError("staff_notification_sender_not_configured")
            for staff_id, address in recipients:
                provider_region = _authorize_provider_region(
                    task, "email", "escalation_notification", str(ticket_id),
                )
                message_sender.send(
                    tenant_id=task.tenant_id, business_id=task.business_id,
                    channel="email", recipient=address,
                    content={
                        "subject": f"{priority.title()} customer escalation",
                        "body": (
                            f"Escalation {ticket_id} needs staff review. "
                            "Open the authenticated dashboard to review the ticket and conversation."
                        ),
                    },
                    idempotency_key=f"escalation:{ticket_id}:staff:{staff_id}:email",
                    message_id=ticket_id, attempt=task.attempts,
                    **({"region_id": provider_region} if provider_region else {}),
                )
            with UnitOfWork(session_factory) as uow:
                conversation = uow.conversations.get(
                    tenant_id=task.tenant_id, conversation_id=conversation_id,
                )
                uow.events.add(
                    tenant_id=task.tenant_id, business_id=task.business_id,
                    conversation_id=conversation_id,
                    event_type="human.escalation.notified",
                    payload={"ticket_id": str(ticket_id), "recipient_count": len(recipients)},
                    source="escalation_notification_worker",
                    correlation_id=(getattr(conversation, "correlation_id", None)
                                    or task.correlation_id),
                )
            return {"ticket_id": str(ticket_id), "status": "notified",
                    "notified": len(recipients)}
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
        if task.task_name == "scheduling.propose_for_lead":
            campaign_id = UUID(str(task.payload["campaign_id"]))
            enrollment_id = UUID(str(task.payload["enrollment_id"]))
            lead_id = UUID(str(task.payload["lead_id"]))
            with UnitOfWork(session_factory) as uow:
                campaign = uow.campaigns.get(
                    tenant_id=task.tenant_id, business_id=task.business_id,
                    campaign_id=campaign_id,
                )
                enrollment = uow.campaign_enrollments.get(
                    tenant_id=task.tenant_id, business_id=task.business_id,
                    campaign_id=campaign_id, enrollment_id=enrollment_id,
                )
                lead = uow.leads.get(
                    tenant_id=task.tenant_id, business_id=task.business_id,
                    lead_id=lead_id,
                )
                if campaign is None or enrollment is None or lead is None:
                    raise LookupError("outreach appointment context is unavailable")
                contact = uow.contacts.get(tenant_id=task.tenant_id, contact_id=lead.contact_id)
                if contact is None or contact.business_id != task.business_id:
                    raise LookupError("outreach appointment contact is unavailable")
                correlated = next((row for row in uow.conversations.list_for_contact(
                    tenant_id=task.tenant_id, contact_id=contact.id,
                ) if row.correlation_id == task.correlation_id), None)
                if correlated is not None:
                    appointment = uow.appointments.for_conversation(
                        tenant_id=task.tenant_id, business_id=task.business_id,
                        conversation_id=correlated.id,
                    )
                    return {"status": "already_processed",
                            "appointment_id": str(appointment.id) if appointment else None}
                conversation = uow.conversations.create(
                    tenant_id=task.tenant_id, business_id=task.business_id,
                    contact_id=contact.id, channel=campaign.channel,
                    correlation_id=task.correlation_id,
                )
                uow.workflows.append(
                    tenant_id=task.tenant_id, conversation_id=conversation.id,
                    state="outreach_appointment_requested",
                    context={"campaign_id": str(campaign_id), "reply_id": task.payload["reply_id"]},
                )
                uow.events.add(
                    tenant_id=task.tenant_id, business_id=task.business_id,
                    conversation_id=conversation.id,
                    event_type="outreach.appointment.requested",
                    payload={"campaign_id": str(campaign_id), "lead_id": str(lead_id)},
                    source="outreach_workflow", correlation_id=task.correlation_id,
                )
                services = uow.services.list_active(
                    tenant_id=task.tenant_id, business_id=task.business_id,
                )
                chosen_service = services[0] if len(services) == 1 else None
                result = (SchedulingService(
                    calendar_provider=calendar_provider, public_app_url=settings.public_app_url,
                ).propose(
                    uow=uow, tenant_id=task.tenant_id, business_id=task.business_id,
                    contact_id=contact.id, conversation_id=conversation.id,
                    text=chosen_service.name if chosen_service else "",
                    now=datetime.now(UTC), contact_timezone=contact.timezone,
                    source="outreach", campaign_id=campaign_id,
                    campaign_enrollment_id=enrollment_id,
                    preferred_date=task.payload.get("preferences", {}).get("date_preference"),
                    preferred_weekday=task.payload.get("preferences", {}).get("weekday_preference"),
                    preferred_time=task.payload.get("preferences", {}).get("time_preference"),
                ) if chosen_service else None)
                if result is None:
                    ticket = EscalationTicketService().create_ticket(
                        uow=uow, tenant_id=task.tenant_id, business_id=task.business_id,
                        contact_id=contact.id, conversation_id=conversation.id,
                        reason="outreach appointment needs human scheduling assistance",
                        priority="normal",
                    )
                    return {"status": "escalated", "ticket_id": str(ticket.id)}
                appointment = result.appointment
                uow.workflows.append(
                    tenant_id=task.tenant_id, conversation_id=conversation.id,
                    state="appointment_proposed",
                    context={"appointment_id": str(appointment.id), "source": "outreach"},
                )
                uow.events.add(
                    tenant_id=task.tenant_id, business_id=task.business_id,
                    conversation_id=conversation.id, event_type="appointment.proposed",
                    payload={"appointment_id": str(appointment.id), "source": "outreach"},
                    source="outreach_workflow", correlation_id=task.correlation_id,
                )
                uow.campaign_events.add(
                    tenant_id=task.tenant_id, business_id=task.business_id,
                    campaign_id=campaign_id, enrollment_id=enrollment_id,
                    event_type="campaign.appointment.proposed",
                    payload={"appointment_id": str(appointment.id)},
                )
                stable_message_id = uuid5(
                    NAMESPACE_URL, f"campaign-reply-confirmation:{task.payload['reply_id']}",
                )
                outbox_message = OutboundDeliveryService().queue(
                    uow, tenant=uow.tenants.get(tenant_id=task.tenant_id),
                    business=uow.businesses.get(tenant_id=task.tenant_id, business_id=task.business_id),
                    contact=contact, conversation=conversation,
                    channel=campaign.channel, content=result.response,
                    message_id=stable_message_id, correlation_id=task.correlation_id,
                )
                proposed_appointment_id = appointment.id
                message_delivery_status = outbox_message.delivery_status
            return {"status": "proposed", "appointment_id": str(proposed_appointment_id),
                    "message_status": message_delivery_status}
        if task.task_name == "appointment.followup.prepare":
            appointment_id = UUID(str(task.payload["appointment_id"]))
            return FollowUpService(
                uow_factory=lambda: UnitOfWork(session_factory),
            ).execute(
                tenant_id=task.tenant_id, business_id=task.business_id,
                appointment_id=appointment_id, correlation_id=task.correlation_id,
            )
        if task.task_name == "appointment.reminder.prepare":
            appointment_id = UUID(str(task.payload["appointment_id"]))
            reminder = str(task.payload["reminder"])
            with UnitOfWork(session_factory) as uow:
                return AppointmentReminderService().prepare(
                    uow, tenant_id=task.tenant_id, business_id=task.business_id,
                    appointment_id=appointment_id, reminder=reminder,
                    correlation_id=task.correlation_id,
                )
        if task.task_name == "scheduling.calendar_sync":
            appointment_id = UUID(str(task.payload["appointment_id"]))
            operation = str(task.payload["operation"])
            if settings.workflow_dry_run:
                with UnitOfWork(session_factory) as uow:
                    appointment = uow.appointments.get_for_tenant(
                        tenant_id=task.tenant_id, business_id=task.business_id,
                        appointment_id=appointment_id,
                    )
                    if appointment is None:
                        raise LookupError("appointment is unavailable")
                    uow.provider_interactions.add(
                        tenant_id=task.tenant_id, business_id=task.business_id,
                        provider="google_calendar", operation=f"{operation}_event",
                        idempotency_key=task.idempotency_key, outcome="dry_run",
                    )
                    uow.events.add(
                        tenant_id=task.tenant_id, business_id=task.business_id,
                        conversation_id=appointment.conversation_id,
                        event_type="calendar.sync.dry_run",
                        payload={"appointment_id": str(appointment_id), "operation": operation},
                        source="calendar_sync_worker", correlation_id=task.correlation_id,
                    )
                return {"appointment_id": str(appointment_id), "status": "dry_run",
                        "operation": operation}
            if calendar_provider is None:
                raise RuntimeError("calendar_provider_not_configured")
            with UnitOfWork(session_factory) as uow:
                appointment = uow.appointments.get_for_tenant(
                    tenant_id=task.tenant_id, business_id=task.business_id,
                    appointment_id=appointment_id,
                )
                if appointment is None:
                    raise LookupError("appointment is unavailable")
                contact = uow.contacts.get(tenant_id=task.tenant_id, contact_id=appointment.contact_id)
                if contact is None or contact.business_id != task.business_id:
                    raise LookupError("appointment contact is unavailable")
                appointment_snapshot = {
                    "id": str(appointment.id), "starts_at": appointment.starts_at,
                    "ends_at": appointment.ends_at, "calendar_event_id": appointment.calendar_event_id,
                    "contact_name": contact.full_name or "Customer", "email": contact.email,
                    "status": appointment.status,
                }
            provider_region = _authorize_provider_region(
                task, "calendar", "appointment", str(appointment_id),
            )
            calendar_region_kwargs = ({"region_id": provider_region}
                                      if provider_region and getattr(
                                          calendar_provider, "regional_provider", False,
                                      ) else {})
            if operation == "cancel":
                if appointment_snapshot["calendar_event_id"]:
                    calendar_provider.cancel_event(
                        tenant_id=task.tenant_id, business_id=task.business_id,
                        event_id=appointment_snapshot["calendar_event_id"],
                        idempotency_key=task.idempotency_key, **calendar_region_kwargs,
                    )
                return {"appointment_id": str(appointment_id), "status": "calendar_cancelled"}
            event = {
                "summary": f"Appointment with {appointment_snapshot['contact_name']}",
                "start": {"dateTime": appointment_snapshot["starts_at"].isoformat()},
                "end": {"dateTime": appointment_snapshot["ends_at"].isoformat()},
            }
            if appointment_snapshot["email"]:
                event["attendees"] = [{"email": appointment_snapshot["email"]}]
            result = calendar_provider.create_event(
                tenant_id=task.tenant_id, business_id=task.business_id,
                event=event, idempotency_key=task.idempotency_key,
                **calendar_region_kwargs,
            )
            event_id = result.get("id")
            if not event_id:
                raise RuntimeError("calendar_response_missing_event_id")
            with UnitOfWork(session_factory) as uow:
                appointment = uow.appointments.get_for_tenant(
                    tenant_id=task.tenant_id, business_id=task.business_id,
                    appointment_id=appointment_id,
                )
                if appointment is None:
                    raise LookupError("appointment is unavailable")
                appointment.calendar_event_id = str(event_id)
            return {"appointment_id": str(appointment_id), "status": "calendar_created"}
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
        if task.task_name == "crm.sync_activity":
            if settings.workflow_dry_run:
                with UnitOfWork(session_factory) as uow:
                    uow.provider_interactions.add(
                        tenant_id=task.tenant_id, business_id=task.business_id,
                        provider="crm", operation="sync_activity",
                        idempotency_key=task.idempotency_key, outcome="dry_run",
                    )
                    uow.events.add(
                        tenant_id=task.tenant_id, business_id=task.business_id,
                        conversation_id=UUID(str(task.payload["conversation_id"])),
                        event_type="crm.activity.dry_run",
                        payload={"appointment_id": task.payload["appointment_id"],
                                 "activity_type": task.payload["activity_type"]},
                        source="crm_sync_worker", correlation_id=task.correlation_id,
                    )
                return {"status": "dry_run"}
            if crm_provider is None:
                with UnitOfWork(session_factory) as uow:
                    uow.events.add(
                        tenant_id=task.tenant_id, business_id=task.business_id,
                        conversation_id=UUID(str(task.payload["conversation_id"])),
                        event_type="crm.activity.skipped",
                        payload={"appointment_id": task.payload["appointment_id"],
                                 "reason_code": "crm_provider_not_configured"},
                        source="crm_sync_worker", correlation_id=task.correlation_id,
                    )
                return {"status": "skipped", "reason": "crm_provider_not_configured"}
            activity_type = str(task.payload["activity_type"])
            engine = CRMSyncEngine(
                provider=crm_provider,
                uow_factory=lambda: UnitOfWork(session_factory),
                business_id=task.business_id,
            )
            result = engine.sync_outbound(
                _trusted_worker(task, "crm_sync"),
                CRMEvent(
                    entity_type="conversation",
                    entity_id=str(task.payload["conversation_id"]),
                    event_type=CRMEventType.OUTREACH_ACTIVITY,
                    idempotency_key=task.idempotency_key,
                    payload={
                        "activity_type": activity_type,
                        "appointment_id": str(task.payload["appointment_id"]),
                    },
                ),
            )
            if result.status == "failed":
                raise RuntimeError("crm_provider_failed")
            if (result.status == "synced" and result.external_id
                    and task.payload.get("appointment_id")):
                with UnitOfWork(session_factory) as uow:
                    appointment = uow.appointments.get_for_tenant(
                        tenant_id=task.tenant_id, business_id=task.business_id,
                        appointment_id=UUID(str(task.payload["appointment_id"])),
                    )
                    if appointment is None:
                        raise LookupError("CRM appointment reference is unavailable")
                    appointment.crm_record_id = result.external_id
            return {"status": result.status, "crm_record_id": str(result.id)}
        if task.task_name != "analytics.refresh_campaign":
            raise LookupError("unsupported analytics task")
        result = CampaignAnalyticsService(
            trusted_tenant_id=task.tenant_id, trusted_business_id=task.business_id,
            uow_factory=lambda: UnitOfWork(session_factory),
        ).get(UUID(str(task.payload["campaign_id"])))
        return result

    handlers.register("inbound", "inbound.process", inbound)
    handlers.register("outreach", "outreach.execute_send", outreach)
    handlers.register("outreach", "outreach.trigger_enrollment", outreach)
    handlers.register("outreach", "communication.deliver_message", outreach)
    handlers.register("scheduling", "scheduling.confirm_appointment", scheduling)
    handlers.register("scheduling", "scheduling.propose_for_lead", scheduling)
    handlers.register("scheduling", "appointment.reminder.prepare", scheduling)
    handlers.register("scheduling", "appointment.followup.prepare", scheduling)
    handlers.register("scheduling", "scheduling.calendar_sync", scheduling)
    handlers.register("escalation", "escalation.sweep", escalation)
    handlers.register("escalation", "escalation.notify_staff", escalation)
    handlers.register("analytics", "crm.sync_activity", analytics)
    handlers.register("analytics", "analytics.refresh_campaign", analytics)
    for domain in ("inbound", "outreach", "scheduling", "escalation", "analytics"):
        handlers.register(domain, "events.replay", replay_event)
    # Calendar and reminder tasks use injected tenant-scoped adapters and the
    # same durable retry/DLQ lifecycle as the other domain handlers.
    return handlers


__all__ = ["build_default_handlers"]
