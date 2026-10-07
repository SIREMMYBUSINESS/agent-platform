"""Tenant-scoped human escalation resolution and optional customer response."""
from uuid import UUID, NAMESPACE_URL, uuid5

from app.security import AuthenticatedPrincipal
from app.services.outbound_delivery_service import OutboundDeliveryService


class EscalationWorkflowService:
    def __init__(self, *, uow_factory, outbound_delivery=None):
        self._uow_factory = uow_factory
        self._delivery = outbound_delivery or OutboundDeliveryService()

    def list_tickets(self, principal: AuthenticatedPrincipal, *, status: str | None = "open",
                     offset: int = 0, limit: int = 100) -> list[dict]:
        if "workflow_executor" not in principal.roles:
            raise PermissionError("workflow access denied")
        with self._uow_factory() as uow:
            rows = uow.escalations.list_for_tenant(
                tenant_id=principal.tenant_id, status=status,
                offset=offset, limit=limit,
            )
            return [{
                "id": row.id, "business_id": row.business_id,
                "contact_id": row.contact_id, "conversation_id": row.conversation_id,
                "reason": row.reason, "priority": row.priority,
                "status": row.status, "created_at": row.created_at,
            } for row in rows]

    def resolve(self, principal: AuthenticatedPrincipal, ticket_id: UUID, *, resolution: str,
                response_to_contact: str | None = None) -> dict:
        if "workflow_executor" not in principal.roles:
            raise PermissionError("workflow access denied")
        with self._uow_factory() as uow:
            ticket = uow.escalations.get_in_tenant(
                tenant_id=principal.tenant_id, ticket_id=ticket_id,
            )
            if ticket is None:
                raise LookupError("escalation not found")
            resolved = uow.escalations.resolve(
                tenant_id=principal.tenant_id, business_id=ticket.business_id,
                ticket_id=ticket_id, resolution=resolution,
            )
            if resolved is None:
                raise ValueError("escalation is already resolved")
            conversation = uow.conversations.get(
                tenant_id=principal.tenant_id, conversation_id=ticket.conversation_id,
            )
            correlation_id = (getattr(conversation, "correlation_id", None)
                              or str(ticket.conversation_id))
            uow.events.add(
                tenant_id=principal.tenant_id, business_id=ticket.business_id,
                conversation_id=ticket.conversation_id, event_type="escalation.resolved",
                payload={"ticket_id": str(ticket_id)}, source="workflow_api",
                correlation_id=correlation_id,
            )
            delivery = None
            if response_to_contact:
                contact = uow.contacts.get(tenant_id=principal.tenant_id, contact_id=ticket.contact_id)
                tenant = uow.tenants.get(tenant_id=principal.tenant_id)
                business = uow.businesses.get(
                    tenant_id=principal.tenant_id, business_id=ticket.business_id,
                )
                if not all((contact, conversation, tenant, business)):
                    raise LookupError("escalation contact context is unavailable")
                message = self._delivery.queue(
                    uow, tenant=tenant, business=business, contact=contact,
                    conversation=conversation, channel=conversation.channel,
                    content=response_to_contact,
                    message_id=uuid5(NAMESPACE_URL, f"escalation:{ticket_id}:response:{response_to_contact}"),
                    correlation_id=correlation_id,
                )
                delivery = message.delivery_status
                uow.events.add(
                    tenant_id=principal.tenant_id, business_id=ticket.business_id,
                    conversation_id=ticket.conversation_id,
                    event_type="escalation.response.queued",
                    payload={"ticket_id": str(ticket_id), "message_id": str(message.id),
                             "delivery_status": delivery}, source="workflow_api",
                    correlation_id=correlation_id,
                )
            return {"id": str(resolved.id), "status": resolved.status,
                    "resolved_at": resolved.resolved_at, "response_delivery_status": delivery}


__all__ = ["EscalationWorkflowService"]
