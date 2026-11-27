"""Staging-only, staff-attributed SMS composer for assigned conversations."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth.dependencies import get_authenticated_principal
from app.config import settings
from app.persistence.session import session_factory
from app.persistence.unit_of_work import UnitOfWork
from app.security import AuthenticatedPrincipal
from app.services.outbound_delivery_service import OutboundDeliveryService
from app.services.workspace_staff_mapping_service import resolve_staff_id, workspace_user_id_from_principal
from app.workflows.business_hours import BusinessHoursService


router = APIRouter(tags=["conversation-sms"])


class ConversationSMSRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=10000)
    client_key: str = Field(min_length=1, max_length=200)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value

    @field_validator("client_key")
    @classmethod
    def normalize_client_key(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("client_key must not be blank")
        return value


class ConversationSMSMessageView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: str
    conversation_id: UUID
    sender: str
    channel: str
    content: str
    direction: str
    delivery_status: str
    delivery_error_code: str | None
    sent_by_staff_id: UUID | None
    created_at: datetime


def require_staging_sms_composer() -> None:
    """Hide the endpoint outside explicitly enabled staging deployments."""
    if (
        settings.environment.strip().casefold() != "staging"
        or not settings.conversation_sms_composer_enabled
    ):
        raise HTTPException(status_code=404, detail="Not found")


def get_conversation_sms_uow_factory():
    return lambda: UnitOfWork(session_factory)


def get_business_hours_service() -> BusinessHoursService:
    return BusinessHoursService()


def get_outbound_delivery_service() -> OutboundDeliveryService:
    return OutboundDeliveryService()


def utcnow() -> datetime:
    return datetime.now(UTC)


def _message_json(message) -> dict:
    return ConversationSMSMessageView.model_validate(message).model_dump(mode="json")


def _refusal(
    uow: UnitOfWork,
    principal: AuthenticatedPrincipal,
    *,
    business_id: str,
    conversation_id: UUID | None,
    requested_conversation_id: UUID,
    staff_id: UUID,
    reason_code: str,
    correlation_id: str | None,
) -> None:
    """Persist an eligibility refusal in the same UoW as the decision."""
    payload = {
        "actor_id": principal.principal_id,
        "staff_id": str(staff_id),
        "reason_code": reason_code,
    }
    if conversation_id is None:
        payload["requested_conversation_id"] = str(requested_conversation_id)
    uow.events.add(
        tenant_id=principal.tenant_id,
        business_id=business_id,
        conversation_id=conversation_id,
        event_type="conversation.sms.refused",
        payload=payload,
        source="conversation_sms_composer",
        correlation_id=correlation_id or str(requested_conversation_id),
    )


def _refusal_response(status_code: int, detail: str, reason_code: str, **extra) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail, "reason_code": reason_code, **extra},
    )


@router.post(
    "/businesses/{business_id}/conversations/{conversation_id}/sms",
    status_code=202,
    response_model=ConversationSMSMessageView,
)
def send_conversation_sms(
    request: Request,
    business_id: str,
    conversation_id: UUID,
    body: ConversationSMSRequest,
    _enabled: None = Depends(require_staging_sms_composer),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    uow_factory=Depends(get_conversation_sms_uow_factory),
    business_hours: BusinessHoursService = Depends(get_business_hours_service),
    outbound_delivery: OutboundDeliveryService = Depends(get_outbound_delivery_service),
) -> Response:
    # get_authenticated_principal verifies the workspace session, active membership,
    # and email verification. API keys and non-workspace principals fail this gate.
    if (
        not isinstance(principal, AuthenticatedPrincipal)
        or workspace_user_id_from_principal(principal) is None
        or "conversation_writer" not in principal.roles
    ):
        raise HTTPException(
            status_code=403,
            detail="A verified workspace user with conversation_writer access is required.",
        )

    with uow_factory() as uow:
        staff_id = resolve_staff_id(uow, principal, business_id=business_id)
        if staff_id is None:
            raise HTTPException(
                status_code=403,
                detail="No staff mapping for this workspace user.",
            )

        # ConversationRepository scopes by tenant. The business assertion is
        # therefore checked explicitly to hide cross-business records as 404.
        conversation = uow.conversations.get(
            tenant_id=principal.tenant_id,
            conversation_id=conversation_id,
        )
        if conversation is None or conversation.business_id != business_id:
            _refusal(
                uow, principal, business_id=business_id, conversation_id=None,
                requested_conversation_id=conversation_id, staff_id=staff_id,
                reason_code="conversation_not_found", correlation_id=request.headers.get("X-Correlation-ID"),
            )
            return _refusal_response(404, "Conversation not found.", "conversation_not_found")

        business = uow.businesses.get(
            tenant_id=principal.tenant_id, business_id=business_id,
        )
        if business is None:
            _refusal(
                uow, principal, business_id=business_id, conversation_id=None,
                requested_conversation_id=conversation_id, staff_id=staff_id,
                reason_code="business_not_found", correlation_id=request.headers.get("X-Correlation-ID"),
            )
            return _refusal_response(404, "Conversation not found.", "conversation_not_found")

        contact = uow.contacts.get(
            tenant_id=principal.tenant_id,
            contact_id=conversation.contact_id,
        )
        if (
            contact is None
            or conversation.contact_id != contact.id
            or contact.business_id != business_id
        ):
            _refusal(
                uow, principal, business_id=business_id, conversation_id=conversation.id,
                requested_conversation_id=conversation_id, staff_id=staff_id,
                reason_code="contact_not_found", correlation_id=request.headers.get("X-Correlation-ID"),
            )
            return _refusal_response(404, "Conversation contact not found.", "contact_not_found")

        tenant = uow.tenants.get(tenant_id=principal.tenant_id)
        if tenant is None:
            _refusal(
                uow, principal, business_id=business_id, conversation_id=conversation.id,
                requested_conversation_id=conversation_id, staff_id=staff_id,
                reason_code="tenant_not_found", correlation_id=request.headers.get("X-Correlation-ID"),
            )
            return _refusal_response(404, "Conversation not found.", "conversation_not_found")

        assignment = uow.conversation_assignments.get(
            tenant_id=principal.tenant_id,
            conversation_id=conversation.id,
        )
        assigned_staff_id = assignment.staff_id if assignment is not None else None
        if assigned_staff_id is None:
            _refusal(
                uow, principal, business_id=business_id, conversation_id=conversation.id,
                requested_conversation_id=conversation_id, staff_id=staff_id,
                reason_code="conversation_unassigned", correlation_id=request.headers.get("X-Correlation-ID"),
            )
            return _refusal_response(
                409, "Assign this conversation before sending.", "conversation_unassigned",
            )
        if assigned_staff_id != staff_id:
            _refusal(
                uow, principal, business_id=business_id, conversation_id=conversation.id,
                requested_conversation_id=conversation_id, staff_id=staff_id,
                reason_code="conversation_assigned_to_another_staff",
                correlation_id=request.headers.get("X-Correlation-ID"),
            )
            return _refusal_response(
                403, "Only the assigned staff member can send.",
                "conversation_assigned_to_another_staff",
            )

        rules = uow.routing_rules.list_for_business(
            tenant_id=principal.tenant_id, business_id=business_id,
        )
        if not any(rule.active for rule in rules):
            _refusal(
                uow, principal, business_id=business_id, conversation_id=conversation.id,
                requested_conversation_id=conversation_id, staff_id=staff_id,
                reason_code="business_hours_not_configured",
                correlation_id=request.headers.get("X-Correlation-ID"),
            )
            return _refusal_response(
                409, "Business hours are not configured.",
                "business_hours_not_configured",
            )

        now = utcnow()
        decision = business_hours.evaluate(
            principal, uow=uow, business_id=business_id, now=now,
        )
        if not decision.safely_configured:
            _refusal(
                uow, principal, business_id=business_id, conversation_id=conversation.id,
                requested_conversation_id=conversation_id, staff_id=staff_id,
                reason_code="business_hours_not_configured",
                correlation_id=request.headers.get("X-Correlation-ID"),
            )
            return _refusal_response(
                409, "Business hours are not safely configured.",
                "business_hours_not_configured",
            )
        if not decision.is_open:
            next_time = business_hours.next_business_time(
                principal, uow=uow, business_id=business_id, after=now,
            )
            _refusal(
                uow, principal, business_id=business_id, conversation_id=conversation.id,
                requested_conversation_id=conversation_id, staff_id=staff_id,
                reason_code="outside_business_hours", correlation_id=request.headers.get("X-Correlation-ID"),
            )
            return _refusal_response(
                409,
                "This conversation cannot be messaged outside business hours.",
                "outside_business_hours",
                next_business_time=next_time.isoformat() if next_time is not None else None,
            )

        message_id = uuid5(
            NAMESPACE_URL,
            f"{principal.tenant_id}:{business_id}:{conversation_id}:{body.client_key}",
        )
        request_correlation_id = request.headers.get("X-Correlation-ID")
        delivery_correlation_id = request_correlation_id or str(message_id)
        try:
            message = outbound_delivery.queue(
                uow,
                tenant=tenant,
                business=business,
                contact=contact,
                conversation=conversation,
                channel="sms",
                content=body.content,
                message_id=message_id,
                correlation_id=delivery_correlation_id,
                sent_by_staff_id=staff_id,
            )
        except ValueError:
            return _refusal_response(
                409,
                "Idempotency key was already used with different content.",
                "idempotency_key_conflict",
            )

        if message.delivery_status == "blocked":
            reason_code = message.delivery_error_code or "outbound_policy_blocked"
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "Outbound SMS is blocked by messaging policy.",
                    "reason_code": reason_code,
                    "message": _message_json(message),
                },
            )

        return JSONResponse(status_code=202, content=_message_json(message))


__all__ = ["router"]
