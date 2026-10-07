"""Verified provider ingress endpoints with tenant-scoped durable idempotency."""
from __future__ import annotations

import hashlib
import hmac
import base64
import json
from datetime import UTC, datetime
from email.utils import parseaddr
from urllib.parse import parse_qsl
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from app.config import settings
from app.integrations.provider_support import DirectoryTenantConfigResolver, ProviderConfigurationError
from app.integrations.sms.twilio_webhooks import validate_twilio_signature
from app.persistence.session import session_factory
from app.persistence.unit_of_work import UnitOfWork
from app.services.policy_service import PolicyService
from app.services.opt_out_service import OptOutService
from app.services.reply_parser_service import ReplyClassification, ReplyParserService
from app.queue.contracts import QueueDomain, QueueTaskSubmission
from app.queue.service import QueueTaskService
from app.security import AuthenticatedPrincipal


router = APIRouter(prefix="/webhooks", tags=["provider-webhooks"])
MAX_BODY = 65_536


def _scoped_config(tenant_id: str, business_id: str):
    if not settings.provider_secrets_dir:
        raise HTTPException(503, "Provider webhook is not configured")
    try:
        return DirectoryTenantConfigResolver(settings.provider_secrets_dir).resolve(
            tenant_id=tenant_id, business_id=business_id,
        )
    except ProviderConfigurationError:
        raise HTTPException(403, "Provider webhook authentication failed") from None


async def _body(request: Request) -> bytes:
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise HTTPException(413, "Provider callback is too large")
    return raw


def _verify_hmac(raw: bytes, signature: str, secret: str) -> bool:
    supplied = signature.removeprefix("sha256=").strip()
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return bool(secret and hmac.compare_digest(expected, supplied))


def _verify_sendgrid(raw: bytes, signature: str, timestamp: str, public_key: str) -> bool:
    if not signature or not timestamp or not public_key:
        return False
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        key = serialization.load_pem_public_key(public_key.encode())
        key.verify(base64.b64decode(signature, validate=True), timestamp.encode() + raw,
                   ec.ECDSA(hashes.SHA256()))
        return True
    except Exception:
        return False


def _persist(tenant_id: str, business_id: str, provider: str, event_key: str,
             event_type: str, payload: dict, result: dict):
    with UnitOfWork(session_factory) as uow:
        row, created = uow.provider_webhooks.record(
            tenant_id=tenant_id, business_id=business_id, provider=provider,
            event_key=event_key, event_type=event_type, payload=payload,
            status="processed", result=result,
        )
        return {"id": row.id, "result": dict(row.result)}, created


@router.post("/sms", status_code=202)
async def inbound_sms(request: Request):
    tenant_id, business_id = request.query_params.get("tenant_id", ""), request.query_params.get("business_id", "")
    if not tenant_id or not business_id:
        raise HTTPException(400, "Provider scope is required")
    raw = await _body(request)
    try:
        pairs = parse_qsl(raw.decode("utf-8"), keep_blank_values=True, max_num_fields=100)
    except (UnicodeDecodeError, ValueError):
        raise HTTPException(400, "Invalid provider callback") from None
    params: dict[str, list[str]] = {}
    for key, value in pairs:
        params.setdefault(key, []).append(value)
    values = {key: all_values[-1] for key, all_values in params.items()}
    config = _scoped_config(tenant_id, business_id)
    expected_url = config.get("sms_inbound_webhook_url", "")
    if (not expected_url.startswith("https://")
            or values.get("AccountSid") != config.get("account_sid")
            or not validate_twilio_signature(
                callback_url=expected_url, parameters=params,
                signature=request.headers.get("x-twilio-signature", ""),
                auth_token=config.get("auth_token", ""),
            )):
        raise HTTPException(403, "Provider callback authentication failed")
    sender, recipient = values.get("From", ""), values.get("To", "")
    external_id, body = values.get("MessageSid", ""), values.get("Body", "")
    if not sender or not recipient or not external_id or len(body) > 20_000:
        raise HTTPException(400, "Provider callback is missing required fields")
    if recipient not in {config.get("from_sms", ""), config.get("messaging_service_number", "")}:
        raise HTTPException(403, "Provider destination is not configured for this business")
    with UnitOfWork(session_factory) as uow:
        contact = uow.contacts.get_by_destination(
            tenant_id=tenant_id, business_id=business_id, channel="sms", address=sender,
        )
        if contact is None:
            raise HTTPException(404, "Inbound contact is not provisioned")
        contact_id = contact.id
        contact_name = contact.full_name
    if ReplyParserService().classify(body) == ReplyClassification.OPT_OUT:
        with UnitOfWork(session_factory) as uow:
            existing = uow.provider_webhooks.get_by_key(
                tenant_id=tenant_id, business_id=business_id,
                provider="twilio", event_key=external_id,
            )
            if existing is not None:
                event_id, created = existing.id, False
            else:
                canceled_enrollments = OptOutService().apply_contact(
                    uow, tenant_id=tenant_id, business_id=business_id,
                    contact_id=contact_id, channel="sms", source="twilio_inbound_opt_out",
                )
                event, created = uow.provider_webhooks.record(
                    tenant_id=tenant_id, business_id=business_id, provider="twilio",
                    event_key=external_id, event_type="sms.opt_out",
                    payload={"MessageSid": external_id, "From": sender, "To": recipient,
                             "Body": body}, status="processed",
                    result={"consent_status": "revoked",
                            "canceled_enrollments": canceled_enrollments},
                )
                event_id = event.id
        return {"accepted": True, "duplicate": not created, "event_id": str(event_id),
                "consent_status": "revoked"}
    principal = AuthenticatedPrincipal(
        principal_id=f"twilio:{config['account_sid']}", tenant_id=tenant_id,
        roles=("inbound_writer",), auth_source="twilio_webhook", issued_at=datetime.now(UTC),
    )
    try:
        task = QueueTaskService(uow_factory=lambda: UnitOfWork(session_factory)).enqueue(
        principal, QueueTaskSubmission(
                domain=QueueDomain.INBOUND, task_name="inbound.process",
                business_id=business_id, idempotency_key=f"twilio-inbound:{external_id}",
                aggregate_type="contact", aggregate_id=contact_id,
                payload={"contact_id": contact_id, "channel": "sms", "text": body,
                         "contact_name": contact_name,
                         "external_message_id": external_id}, correlation_id=uuid4().hex,
            ),
        )
    except Exception:
        raise HTTPException(503, "Inbound message could not be queued") from None
    stored, created = _persist(
        tenant_id, business_id, "twilio", external_id, "sms.inbound",
        {key: values.get(key) for key in ("MessageSid", "From", "To", "Body", "NumMedia")},
        {"queue_task_id": str(task.id), "status": task.status.value},
    )
    return {"accepted": True, "duplicate": not created, "event_id": str(stored["id"])}


@router.post("/email/inbound", status_code=202)
async def inbound_email(request: Request):
    """Accept a signed tenant-scoped inbound email and queue it as a contact reply."""
    tenant_id = request.query_params.get("tenant_id", "")
    business_id = request.query_params.get("business_id", "")
    if not tenant_id or not business_id:
        raise HTTPException(400, "Provider scope is required")
    raw = await _body(request)
    config = _scoped_config(tenant_id, business_id)
    if not _verify_hmac(
        raw, request.headers.get("x-provider-signature", ""),
        config.get("email_inbound_webhook_secret", ""),
    ):
        raise HTTPException(403, "Provider callback authentication failed")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(400, "Invalid inbound email") from None
    if not isinstance(payload, dict):
        raise HTTPException(400, "Inbound email must be an object")
    sender = parseaddr(str(payload.get("from", "")))[1].strip().casefold()
    recipient = parseaddr(str(payload.get("to", "")))[1].strip().casefold()
    configured_recipient = config.get("inbound_email_address", "").strip().casefold()
    external_id = str(payload.get("message_id", "")).strip().strip("<>")
    body = payload.get("text")
    if (not sender or not recipient or recipient != configured_recipient or not external_id
            or not isinstance(body, str) or not body.strip() or len(body) > 20_000):
        raise HTTPException(400, "Inbound email is missing valid sender, recipient, ID, or text")
    with UnitOfWork(session_factory) as uow:
        contact = uow.contacts.get_by_destination(
            tenant_id=tenant_id, business_id=business_id, channel="email", address=sender,
        )
        if contact is None:
            raise HTTPException(404, "Inbound contact is not provisioned")
        contact_id, contact_name = contact.id, contact.full_name
        duplicate = uow.provider_webhooks.get_by_key(
            tenant_id=tenant_id, business_id=business_id,
            provider="email_inbound", event_key=external_id,
        )
        if duplicate is not None:
            return {"accepted": True, "duplicate": True, "event_id": str(duplicate.id)}
        if ReplyParserService().classify(body) == ReplyClassification.OPT_OUT:
            canceled = OptOutService().apply_contact(
                uow, tenant_id=tenant_id, business_id=business_id,
                contact_id=contact_id, channel="email", source="email_inbound_opt_out",
            )
            event, _ = uow.provider_webhooks.record(
                tenant_id=tenant_id, business_id=business_id, provider="email_inbound",
                event_key=external_id, event_type="email.opt_out",
                payload={"message_id": external_id, "from": sender, "to": recipient,
                         "text": body}, status="processed",
                result={"consent_status": "revoked", "canceled_enrollments": canceled},
            )
            return {"accepted": True, "duplicate": False, "event_id": str(event.id),
                    "consent_status": "revoked"}
    principal = AuthenticatedPrincipal(
        principal_id=f"email-inbound:{tenant_id}:{business_id}", tenant_id=tenant_id,
        roles=("inbound_writer",), auth_source="email_inbound_webhook", issued_at=datetime.now(UTC),
    )
    task = QueueTaskService(uow_factory=lambda: UnitOfWork(session_factory)).enqueue(
        principal, QueueTaskSubmission(
            domain=QueueDomain.INBOUND, task_name="inbound.process", business_id=business_id,
            idempotency_key=f"email-inbound:{external_id}", aggregate_type="contact",
            aggregate_id=contact_id,
            payload={"contact_id": contact_id, "channel": "email", "text": body,
                     "contact_name": contact_name, "external_message_id": external_id},
            correlation_id=uuid4().hex,
        ),
    )
    stored, created = _persist(
        tenant_id, business_id, "email_inbound", external_id, "email.inbound",
        {"message_id": external_id, "from": sender, "to": recipient, "text": body},
        {"queue_task_id": str(task.id), "status": task.status.value},
    )
    return {"accepted": True, "duplicate": not created, "event_id": str(stored["id"])}


async def _authenticated_event(request: Request, kind: str):
    tenant_id, business_id = request.query_params.get("tenant_id", ""), request.query_params.get("business_id", "")
    if not tenant_id or not business_id:
        raise HTTPException(400, "Provider scope is required")
    config = _scoped_config(tenant_id, business_id)
    raw = await _body(request)
    if kind == "call":
        callback_url = config.get("call_webhook_url", "")
        signature = request.headers.get("x-twilio-signature", "")
        try:
            params = dict(parse_qsl(raw.decode("utf-8"), keep_blank_values=True, max_num_fields=100))
        except (UnicodeDecodeError, ValueError):
            raise HTTPException(400, "Invalid provider callback") from None
        valid = callback_url.startswith("https://") and validate_twilio_signature(
            callback_url=callback_url, parameters=params, signature=signature,
            auth_token=config.get("auth_token", ""),
        )
        payload = params
        event_key = params.get("CallSid", "") + ":" + params.get("CallStatus", "unknown")
        event_type = "call.status"
        provider = "twilio"
    else:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HTTPException(400, "Invalid provider callback") from None
        if kind == "email":
            provider = config.get("email_provider", "sendgrid").lower()
            if provider == "sendgrid":
                valid = _verify_sendgrid(
                    raw, request.headers.get("x-twilio-email-event-webhook-signature", ""),
                    request.headers.get("x-twilio-email-event-webhook-timestamp", ""),
                    config.get("email_webhook_public_key", ""),
                )
            else:
                valid = _verify_hmac(raw, request.headers.get("x-provider-signature", ""),
                                     config.get("email_webhook_secret", ""))
            event_key = hashlib.sha256(raw).hexdigest()
            event_type = "email.events"
        else:
            secret = config.get("crm_webhook_secret", "")
            valid = _verify_hmac(raw, request.headers.get("x-provider-signature", ""), secret)
            provider = config.get("crm_provider", "hubspot").lower()
            event_key = hashlib.sha256(raw).hexdigest()
            event_type = "crm.events"
    if not valid:
        raise HTTPException(403, "Provider callback authentication failed")
    if not event_key:
        raise HTTPException(400, "Provider callback is missing event identity")
    if kind == "call":
        # Twilio voice webhooks are policy checked before recording or queueing.
        # Recording is disabled unless business configuration explicitly enables it.
        values = payload
        callback_url = config.get("voicemail_action_url", "") if "TranscriptionText" in values else config.get("call_webhook_url", "")
        sender = values.get("From", "")
        is_transcription = "TranscriptionText" in values and values.get("TranscriptionStatus", "completed") == "completed"
        is_inbound = str(values.get("Direction", "")).lower() == "inbound"
        if (is_transcription or is_inbound) and sender:
            with UnitOfWork(session_factory) as uow:
                contact = uow.contacts.get_by_destination(
                    tenant_id=tenant_id, business_id=business_id,
                    channel="sms", address=sender,
                )
                tenant = uow.tenants.get(tenant_id=tenant_id)
                business = uow.businesses.get(tenant_id=tenant_id, business_id=business_id)
                if contact is None or tenant is None or business is None:
                    raise HTTPException(404, "Inbound caller is not provisioned")
                channel = "voicemail" if is_transcription else "call"
                decision = PolicyService().evaluate(
                    uow, tenant=tenant, business=business, contact=contact,
                    channel=channel, direction="inbound",
                )
                contact_id, contact_name = contact.id, contact.full_name
            if is_transcription and decision.allowed:
                transcript = values.get("TranscriptionText", "")
                if not isinstance(transcript, str) or len(transcript) > 20_000:
                    raise HTTPException(400, "Voicemail transcript is invalid")
                principal = AuthenticatedPrincipal(
                    principal_id=f"twilio:{config.get('account_sid', '')}",
                    tenant_id=tenant_id, roles=("inbound_writer",),
                    auth_source="twilio_webhook", issued_at=datetime.now(UTC),
                )
                from app.queue.service import QueueTaskService
                task = QueueTaskService(uow_factory=lambda: UnitOfWork(session_factory)).enqueue(
                    principal, QueueTaskSubmission(
                        domain=QueueDomain.INBOUND, task_name="inbound.process",
                        business_id=business_id,
                        idempotency_key=f"twilio-voicemail:{values.get('CallSid', event_key)}",
                        aggregate_type="contact", aggregate_id=contact_id,
                        payload={"contact_id": contact_id, "channel": "voicemail",
                                 "text": transcript, "contact_name": contact_name},
                        correlation_id=uuid4().hex,
                    ),
                )
                payload = {key: values.get(key) for key in
                           ("CallSid", "TranscriptionStatus", "TranscriptionText")}
                stored, created = _persist(tenant_id, business_id, provider, event_key,
                                           "voicemail.transcribed", payload,
                                           {"queue_task_id": str(task.id), "status": task.status.value})
                return JSONResponse(status_code=202, content={
                    "accepted": True, "duplicate": not created, "event_id": str(stored["id"]),
                })
            if is_inbound:
                recording = (decision.allowed and config.get("recording_enabled", "false").lower() == "true"
                             and callback_url.startswith("https://"))
                if recording:
                    from xml.sax.saxutils import quoteattr
                    xml = ("<Response><Say>Please leave a message after the tone. This call may be "
                           "recorded and transcribed.</Say>"
                           f"<Record transcribe=\"true\" method=\"POST\" action={quoteattr(callback_url)} /></Response>")
                else:
                    xml = "<Response><Say>We are unable to process this call automatically. Please contact the business directly.</Say><Hangup /></Response>"
                payload = {key: values.get(key) for key in
                           ("CallSid", "Direction", "CallStatus", "From", "To")}
                _persist(tenant_id, business_id, provider, event_key, "call.inbound", payload,
                         {"recording_enabled": recording, "policy_allowed": decision.allowed,
                          "reason_code": decision.reason_code})
                return Response(content=xml, media_type="application/xml", status_code=200)
    if isinstance(payload, list):
        payload = {"events": payload}
    if not isinstance(payload, dict):
        raise HTTPException(400, "Provider callback must be an object")
    stored, created = _persist(
        tenant_id, business_id, provider, event_key, event_type,
        payload, {"accepted": True, "event_type": event_type},
    )
    return JSONResponse(status_code=202, content={
        "accepted": True, "duplicate": not created, "event_id": str(stored["id"]),
    })


@router.post("/email", status_code=202)
async def inbound_email_events(request: Request):
    return await _authenticated_event(request, "email")


@router.post("/call", status_code=202)
async def inbound_call_events(request: Request):
    return await _authenticated_event(request, "call")


@router.post("/crm", status_code=202)
async def inbound_crm_events(request: Request):
    return await _authenticated_event(request, "crm")


__all__ = ["router"]
