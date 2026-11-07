"""Authenticated provider delivery receipt endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qsl
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.auth.dependencies import require_tenant_home_region
from app.config import settings
from app.integrations.provider_support import (
    DirectoryTenantConfigResolver, ProviderConfigurationError,
)
from app.integrations.sms.twilio_webhooks import (
    build_status_callback_url, validate_twilio_signature,
)
from app.security import AuthenticatedPrincipal
from app.services.outreach_execution import (
    OutreachConflict, OutreachExecutionService, OutreachNotFound,
)
from app.persistence.session import session_factory
from app.persistence.unit_of_work import UnitOfWork


router = APIRouter(prefix="/webhooks", tags=["provider-webhooks"])


def get_outreach_service() -> OutreachExecutionService:
    return OutreachExecutionService(uow_factory=lambda: UnitOfWork(session_factory))


@router.post("/twilio/{tenant_id}/{business_id}", status_code=204)
async def twilio_status_callback(
    tenant_id: str,
    business_id: str,
    request: Request,
    message_id: UUID,
    attempt: int,
    service: OutreachExecutionService = Depends(get_outreach_service),
) -> Response:
    """Validate the provider signature before resolving a trusted internal principal."""
    if settings.provider_secrets_dir is None:
        raise HTTPException(503, "Provider webhook is not configured")
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/x-www-form-urlencoded":
        raise HTTPException(415, "Unsupported provider callback content type")
    raw = await request.body()
    if len(raw) > 65536:
        raise HTTPException(413, "Provider callback is too large")
    try:
        pairs = parse_qsl(raw.decode("utf-8"), keep_blank_values=True, max_num_fields=200)
    except (UnicodeDecodeError, ValueError):
        raise HTTPException(400, "Invalid provider callback") from None
    params: dict[str, list[str]] = {}
    for key, value in pairs:
        params.setdefault(key, []).append(value)
    single = {key: values[-1] for key, values in params.items()}
    try:
        config = DirectoryTenantConfigResolver(settings.provider_secrets_dir).resolve(
            tenant_id=tenant_id, business_id=business_id,
        )
        account_sid = config.get("account_sid", "")
        auth_token = config.get("auth_token", "")
        callback_base = config.get("status_callback_base_url", "")
        expected_url = build_status_callback_url(callback_base, tenant_id=tenant_id,
                                                 business_id=business_id, message_id=message_id,
                                                 attempt=attempt)
    except (ProviderConfigurationError, ValueError):
        raise HTTPException(403, "Provider callback authentication failed") from None
    signature = request.headers.get("x-twilio-signature", "")
    accepted_tokens = [auth_token]
    previous_token = config.get("auth_token_previous", "")
    if previous_token:
        accepted_tokens.append(previous_token)
    if (single.get("AccountSid") != account_sid
            or not any(validate_twilio_signature(
                callback_url=expected_url, parameters=params,
                signature=signature, auth_token=token,
            ) for token in accepted_tokens)):
        raise HTTPException(403, "Provider callback authentication failed")

    require_tenant_home_region(tenant_id)

    provider_message_id = single.get("MessageSid", "")
    status = single.get("MessageStatus", single.get("SmsStatus", ""))
    if not provider_message_id or not status:
        raise HTTPException(400, "Provider callback is missing required fields")
    principal = AuthenticatedPrincipal(
        principal_id=f"twilio:{account_sid}", tenant_id=tenant_id,
        roles=("delivery_receipt_writer",), auth_source="twilio_webhook",
        issued_at=datetime.now(UTC),
    )
    try:
        service.apply_twilio_receipt(
            principal, business_id=business_id, message_id=message_id,
            provider_message_id=provider_message_id, attempt=attempt, provider_status=status,
            error_code=single.get("ErrorCode") or None,
        )
    except OutreachNotFound:
        raise HTTPException(404, "Provider delivery target not found") from None
    except OutreachConflict:
        raise HTTPException(409, "Provider delivery receipt conflicts with stored state") from None
    except ValueError:
        raise HTTPException(400, "Invalid provider delivery receipt") from None
    return Response(status_code=204)
