"""Passwordless workspace login; public input never chooses a tenant or a role."""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlmodel import select

from app.auth.dependencies import bearer, get_auth_uow_factory, get_authenticated_principal
from app.auth.magic_link_mailer import SendGridMagicLinkMailer
from app.config import settings
from app.persistence.models import TenantMembership
from app.persistence.session import session_factory
from app.persistence.unit_of_work import UnitOfWork
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
    request: Request,
    mailer=Depends(get_magic_link_mailer),
    uow_factory=Depends(get_auth_uow_factory),
):
    now = _now()
    raw_token = None
    with uow_factory() as uow:
        user = uow.workspace_identity.get_user_by_email(body.email)
        if user is None or user.status not in {"invited", "active"}:
            return {"message": GENERIC_SENT_MESSAGE}
        memberships = uow.workspace_identity.list_login_memberships(user.id)
        if not memberships or mailer is None or not settings.auth_public_origin:
            if not memberships:
                return {"message": GENERIC_SENT_MESSAGE}
            raise HTTPException(503, "Workspace email delivery is not configured")
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
        raise HTTPException(503, "Workspace email delivery is temporarily unavailable") from None
    return {"message": GENERIC_SENT_MESSAGE}


@router.post("/magic-link/verify")
def verify_magic_link(
    body: MagicLinkVerifyRequest,
    uow_factory=Depends(get_auth_uow_factory),
):
    if TOKEN_RE.fullmatch(body.token) is None or not body.token.startswith("ml_"):
        raise HTTPException(401, "This sign-in link is invalid or expired")
    now = _now()
    login_ticket = _token("lt")
    with uow_factory() as uow:
        challenge = uow.workspace_identity.get_magic_link(_digest(body.token), for_update=True)
        if (
            challenge is None or challenge.consumed_at is not None
            or challenge.expires_at <= now
        ):
            raise HTTPException(401, "This sign-in link is invalid or expired")
        user = uow.workspace_identity.get_user(challenge.user_id)
        memberships = uow.workspace_identity.list_login_memberships(challenge.user_id)
        if user is None or user.status not in {"invited", "active"} or not memberships:
            challenge.consumed_at = now
            raise HTTPException(401, "This sign-in link is invalid or expired")
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
    return {"login_ticket": login_ticket, "display_name": display_name, "memberships": choices}


@router.post("/session")
def create_workspace_session(
    body: WorkspaceSessionRequest,
    uow_factory=Depends(get_auth_uow_factory),
):
    if TOKEN_RE.fullmatch(body.login_ticket) is None or not body.login_ticket.startswith("lt_"):
        raise HTTPException(401, "Workspace sign-in has expired. Request a new link.")
    now = _now()
    raw_session_token = _token("ws")
    with uow_factory() as uow:
        ticket = uow.workspace_identity.get_login_ticket(_digest(body.login_ticket), for_update=True)
        if (
            ticket is None or ticket.consumed_at is not None
            or ticket.expires_at <= now
        ):
            raise HTTPException(401, "Workspace sign-in has expired. Request a new link.")
        membership = uow.workspace_identity.get_membership(
            membership_id=body.membership_id,
            user_id=ticket.user_id,
            tenant_id=next(
                (item.tenant_id for item in uow.workspace_identity.list_login_memberships(ticket.user_id)
                 if item.id == body.membership_id),
                "",
            ),
            for_update=True,
        )
        if membership is None or membership.status not in {"active", "invited"}:
            raise HTTPException(403, "Workspace access is unavailable")
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
            expires_at=now + timedelta(seconds=settings.auth_session_ttl_seconds),
        )
        expires_at = now + timedelta(seconds=settings.auth_session_ttl_seconds)
        tenant_id = membership.tenant_id
    return {
        "session_token": raw_session_token,
        "tenant_id": tenant_id,
        "expires_at": expires_at.isoformat(),
    }


@router.get("/me")
def current_workspace_identity(
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    return {
        "principal_id": principal.principal_id,
        "tenant_id": principal.tenant_id,
        "roles": list(principal.roles),
        "auth_source": principal.auth_source,
    }


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
    return _no_store(__import__("fastapi").Response(status_code=204))
