"""Passwordless workspace login; public input never chooses a tenant or a role."""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import quote
from uuid import UUID
from typing import NoReturn

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth.dependencies import bearer, get_auth_uow_factory, get_authenticated_principal
from app.auth.magic_link_mailer import SendGridMagicLinkMailer
from app.config import settings
from app.security import AuthenticatedPrincipal

router = APIRouter(prefix="/auth", tags=["workspace-auth"])
logger = logging.getLogger("app.auth")
GENERIC_SENT_MESSAGE = "If an eligible workspace account exists for that email, a sign-in link will be sent."
TOKEN_RE = re.compile(r"^(ml|lt)_[A-Za-z0-9_-]{43}$")


class MagicLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
            raise ValueError("Enter a valid email address")
        return normalized


class StagingAuthBypassRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    tenant_id: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return MagicLinkRequest.normalize_email(value)


class MagicLinkVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=46, max_length=46)


class WorkspaceSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    login_ticket: str = Field(min_length=46, max_length=46)
    membership_id: UUID


def _token(prefix: str) -> str:
    return prefix + "_" + secrets.token_urlsafe(32)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def get_magic_link_mailer():
    api_key = settings.auth_email_api_key
    from_email = settings.auth_email_from_email
    if not api_key or not from_email:
        return None
    return SendGridMagicLinkMailer(api_key=api_key, from_email=from_email)


def _no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/magic-link", status_code=202)
def request_magic_link(
    body: MagicLinkRequest,
    mailer=Depends(get_magic_link_mailer),
    uow_factory=Depends(get_auth_uow_factory),
):
    if mailer is None or not settings.auth_public_origin:
        raise HTTPException(503, "Workspace email delivery is not configured")
    now = _now()
    raw_token = None
    with uow_factory() as uow:
        user = uow.workspace_identity.get_user_by_email(body.email)
        if user is None or user.status not in {"invited", "active"}:
            return {"message": GENERIC_SENT_MESSAGE}
        memberships = uow.workspace_identity.list_login_memberships(user.id)
        if not memberships:
            return {"message": GENERIC_SENT_MESSAGE}
        if uow.workspace_identity.count_recent_magic_links(
            user_id=user.id, since=now - timedelta(hours=1),
        ) >= 5:
            return {"message": GENERIC_SENT_MESSAGE}
        raw_token = _token("ml")
        uow.workspace_identity.create_magic_link(
            user_id=user.id, token_hash=_digest(raw_token),
            expires_at=now + timedelta(seconds=settings.auth_magic_link_ttl_seconds),
            created_at=now,
        )

    link = (
        settings.auth_public_origin.rstrip("/")
        + "/auth/verify?token=" + quote(raw_token, safe="")
    )
    try:
        mailer.send_magic_link(recipient=body.email, link=link)
    except Exception as exc:
        with uow_factory() as uow:
            challenge = uow.workspace_identity.get_magic_link(_digest(raw_token), for_update=True)
            if challenge is not None:
                challenge.consumed_at = _now()
        logger.warning("workspace magic-link delivery failed", extra={
            "component": "workspace_auth", "operation": "magic_link_delivery",
            "outcome": "failed", "error_type": type(exc).__name__,
        })
        return {"message": GENERIC_SENT_MESSAGE}
    return {"message": GENERIC_SENT_MESSAGE}


def _deny_staging_owner_session(category: str) -> NoReturn:
    """Log a bounded internal denial category without exposing identity state to callers."""
    logger.warning("staging owner session denied", extra={
        "component": "workspace_auth",
        "operation": "staging_owner_session",
        "outcome": "denied",
        "denial_category": category,
    })
    raise HTTPException(403, "Staging workspace access is unavailable")


@router.post("/staging-owner-session")
def create_staging_owner_session(
    body: StagingAuthBypassRequest,
    staging_access_code: str | None = Header(default=None, alias="X-Staging-Auth-Bypass"),
    uow_factory=Depends(get_auth_uow_factory),
):
    """Temporary, explicitly gated owner access available only in the staging environment."""
    if settings.environment.strip().lower() != "staging" or not settings.staging_auth_bypass_enabled:
        raise HTTPException(404, "Not found")
    expected_code = settings.staging_auth_bypass_token or ""
    if (
        not expected_code
        or not staging_access_code
        or not secrets.compare_digest(staging_access_code, expected_code)
    ):
        raise HTTPException(401, "Staging access code is invalid")
    if not settings.staging_auth_bypass_email or not settings.staging_auth_bypass_tenant_id:
        _deny_staging_owner_session("identity_configuration_missing")
    if body.email != settings.staging_auth_bypass_email:
        _deny_staging_owner_session("configured_email_mismatch")
    if body.tenant_id != settings.staging_auth_bypass_tenant_id:
        _deny_staging_owner_session("configured_tenant_mismatch")

    now = _now()
    raw_session_token = _token("ws")
    session_expires_at = now + timedelta(seconds=settings.auth_session_ttl_seconds)
    with uow_factory() as uow:
        user = uow.workspace_identity.get_user_by_email(body.email)
        if user is None:
            _deny_staging_owner_session("user_not_found")
        if user.status not in {"invited", "active"}:
            _deny_staging_owner_session("user_ineligible_status")
        tenant_memberships = [
            item for item in uow.workspace_identity.list_login_memberships(user.id)
            if item.tenant_id == body.tenant_id
        ]
        if not tenant_memberships:
            _deny_staging_owner_session("tenant_membership_not_found")
        owner_memberships = [item for item in tenant_memberships if item.role == "owner"]
        if not owner_memberships:
            _deny_staging_owner_session("owner_role_not_found")
        membership = owner_memberships[0]
        if membership.status not in {"invited", "active"}:
            _deny_staging_owner_session("owner_membership_ineligible_status")
        user.status = "active"
        user.email_verified_at = user.email_verified_at or now
        if membership.status == "invited":
            membership.status = "active"
            membership.updated_at = now
        uow.workspace_identity.create_workspace_session(
            user_id=user.id,
            tenant_id=membership.tenant_id,
            membership_id=membership.id,
            token_hash=_digest(raw_session_token),
            issued_at=now,
            expires_at=session_expires_at,
        )
    return JSONResponse(
        {
            "session_token": raw_session_token,
            "tenant_id": body.tenant_id,
            "expires_at": session_expires_at.isoformat(),
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/magic-link/verify")
def verify_magic_link(
    body: MagicLinkVerifyRequest,
    uow_factory=Depends(get_auth_uow_factory),
):
    if TOKEN_RE.fullmatch(body.token) is None or not body.token.startswith("ml_"):
        raise HTTPException(401, "This sign-in link is invalid or expired")
    now = _now()
    login_ticket = _token("lt")
    invalid_identity = False
    choices = []
    display_name = ""
    with uow_factory() as uow:
        challenge = uow.workspace_identity.get_magic_link(_digest(body.token), for_update=True)
        if (
            challenge is None or challenge.consumed_at is not None
            or _as_utc(challenge.expires_at) <= now
        ):
            raise HTTPException(401, "This sign-in link is invalid or expired")
        user = uow.workspace_identity.get_user(challenge.user_id)
        memberships = uow.workspace_identity.list_login_memberships(challenge.user_id)
        if user is None or user.status not in {"invited", "active"} or not memberships:
            challenge.consumed_at = now
            invalid_identity = True
        else:
            challenge.consumed_at = now
            user.status = "active"
            user.email_verified_at = user.email_verified_at or now
            for membership in memberships:
                if membership.status == "invited":
                    membership.status = "active"
                    membership.updated_at = now
            uow.workspace_identity.create_login_ticket(
                user_id=user.id,
                token_hash=_digest(login_ticket),
                expires_at=now + timedelta(seconds=settings.auth_login_ticket_ttl_seconds),
                created_at=now,
            )
            choices = [
                {
                    "membership_id": str(item.id),
                    "tenant_id": item.tenant_id,
                    "workspace_label": item.workspace_label,
                    "role": item.role,
                }
                for item in memberships
            ]
            display_name = user.display_name
    if invalid_identity:
        raise HTTPException(401, "This sign-in link is invalid or expired")
    return JSONResponse(
        {"login_ticket": login_ticket, "display_name": display_name, "memberships": choices},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/session")
def create_workspace_session(
    body: WorkspaceSessionRequest,
    uow_factory=Depends(get_auth_uow_factory),
):
    if TOKEN_RE.fullmatch(body.login_ticket) is None or not body.login_ticket.startswith("lt_"):
        raise HTTPException(401, "Workspace sign-in has expired. Request a new link.")
    now = _now()
    raw_session_token = _token("ws")
    session_expires_at = now + timedelta(seconds=settings.auth_session_ttl_seconds)
    invalid_user = False
    tenant_id = ""
    with uow_factory() as uow:
        ticket = uow.workspace_identity.get_login_ticket(_digest(body.login_ticket), for_update=True)
        if (
            ticket is None or ticket.consumed_at is not None
            or _as_utc(ticket.expires_at) <= now
        ):
            raise HTTPException(401, "Workspace sign-in has expired. Request a new link.")
        user = uow.workspace_identity.get_user(ticket.user_id)
        memberships = uow.workspace_identity.list_login_memberships(ticket.user_id)
        membership = next((item for item in memberships if item.id == body.membership_id), None)
        if user is None or user.status != "active" or user.email_verified_at is None:
            ticket.consumed_at = now
            invalid_user = True
        elif membership is None or membership.status not in {"active", "invited"}:
            raise HTTPException(403, "Workspace access is unavailable")
        else:
            if membership.status == "invited":
                membership.status = "active"
                membership.updated_at = now
            ticket.consumed_at = now
            uow.workspace_identity.create_workspace_session(
                user_id=ticket.user_id,
                tenant_id=membership.tenant_id,
                membership_id=membership.id,
                token_hash=_digest(raw_session_token),
                issued_at=now,
                expires_at=session_expires_at,
            )
            tenant_id = membership.tenant_id
    if invalid_user:
        raise HTTPException(403, "Workspace access is unavailable")
    return JSONResponse(
        {
            "session_token": raw_session_token,
            "tenant_id": tenant_id,
            "expires_at": session_expires_at.isoformat(),
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/me")
def current_workspace_identity(
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    return JSONResponse(
        {
            "principal_id": principal.principal_id,
            "tenant_id": principal.tenant_id,
            "roles": list(principal.roles),
            "auth_source": principal.auth_source,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/logout", status_code=204)
def logout_workspace_session(
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    credential=Depends(bearer),
    uow_factory=Depends(get_auth_uow_factory),
):
    if principal.auth_source == "email_magic_link" and credential is not None:
        with uow_factory() as uow:
            uow.workspace_identity.revoke_workspace_session(
                _digest(credential.credentials), revoked_at=_now(),
            )
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
