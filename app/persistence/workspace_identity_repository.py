"""Persistence operations for passwordless workspace identity and sessions."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func
from sqlmodel import Session, select

from app.persistence.models import (
    AuthLoginTicket,
    MagicLinkChallenge,
    TenantMembership,
    WorkspaceSession,
    WorkspaceUser,
)


class SQLWorkspaceIdentityRepository:
    """Use the UnitOfWork-owned session; transaction completion stays with UoW."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_user_by_email(self, email: str) -> WorkspaceUser | None:
        return self._session.exec(
            select(WorkspaceUser).where(WorkspaceUser.email == email)
        ).first()

    def get_user(self, user_id: UUID) -> WorkspaceUser | None:
        return self._session.get(WorkspaceUser, user_id)

    def list_login_memberships(self, user_id: UUID) -> list[TenantMembership]:
        statement = select(TenantMembership).where(
            TenantMembership.user_id == user_id,
            TenantMembership.status.in_(("invited", "active")),
        ).order_by(TenantMembership.created_at, TenantMembership.id)
        return list(self._session.exec(statement).all())

    def list_memberships_for_user(self, user_id: UUID) -> list[TenantMembership]:
        """Return all membership states for internal access diagnostics."""
        statement = select(TenantMembership).where(
            TenantMembership.user_id == user_id,
        ).order_by(TenantMembership.created_at, TenantMembership.id)
        return list(self._session.exec(statement).all())

    def get_membership(
        self, *, membership_id: UUID, user_id: UUID, tenant_id: str,
        for_update: bool = False,
    ) -> TenantMembership | None:
        statement = select(TenantMembership).where(
            TenantMembership.id == membership_id,
            TenantMembership.user_id == user_id,
            TenantMembership.tenant_id == tenant_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.exec(statement).first()

    def get_membership_for_user_tenant(
        self, *, user_id: UUID, tenant_id: str,
    ) -> TenantMembership | None:
        statement = select(TenantMembership).where(
            TenantMembership.user_id == user_id,
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.status == "active",
        )
        return self._session.exec(statement).first()

    def count_recent_magic_links(self, *, user_id: UUID, since: datetime) -> int:
        statement = select(func.count()).select_from(MagicLinkChallenge).where(
            MagicLinkChallenge.user_id == user_id,
            MagicLinkChallenge.created_at >= since,
        )
        return int(self._session.exec(statement).one())

    def create_magic_link(
        self, *, user_id: UUID, token_hash: str, expires_at: datetime,
        created_at: datetime,
    ) -> MagicLinkChallenge:
        row = MagicLinkChallenge(
            user_id=user_id, token_hash=token_hash,
            expires_at=expires_at, created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def get_magic_link(
        self, token_hash: str, *, for_update: bool = False,
    ) -> MagicLinkChallenge | None:
        statement = select(MagicLinkChallenge).where(
            MagicLinkChallenge.token_hash == token_hash,
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.exec(statement).first()

    def create_login_ticket(
        self, *, user_id: UUID, token_hash: str, expires_at: datetime,
        created_at: datetime,
    ) -> AuthLoginTicket:
        row = AuthLoginTicket(
            user_id=user_id, token_hash=token_hash,
            expires_at=expires_at, created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def get_login_ticket(
        self, token_hash: str, *, for_update: bool = False,
    ) -> AuthLoginTicket | None:
        statement = select(AuthLoginTicket).where(
            AuthLoginTicket.token_hash == token_hash,
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.exec(statement).first()

    def create_workspace_session(
        self, *, user_id: UUID, tenant_id: str, membership_id: UUID,
        token_hash: str, issued_at: datetime, expires_at: datetime,
    ) -> WorkspaceSession:
        row = WorkspaceSession(
            user_id=user_id, tenant_id=tenant_id, membership_id=membership_id,
            token_hash=token_hash, issued_at=issued_at, expires_at=expires_at,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def find_valid_session(self, token_hash: str):
        statement = (
            select(WorkspaceSession, WorkspaceUser, TenantMembership)
            .join(WorkspaceUser, WorkspaceUser.id == WorkspaceSession.user_id)
            .join(
                TenantMembership,
                (TenantMembership.id == WorkspaceSession.membership_id)
                & (TenantMembership.user_id == WorkspaceSession.user_id)
                & (TenantMembership.tenant_id == WorkspaceSession.tenant_id),
            )
            .where(WorkspaceSession.token_hash == token_hash)
        )
        result = self._session.exec(statement).first()
        if result is None:
            return None
        session, user, membership = result
        if (
            session.revoked_at is not None
            or session.expires_at <= session.issued_at
            or user.status != "active"
            or user.email_verified_at is None
            or membership.status != "active"
        ):
            return None
        return session, user, membership

    def revoke_workspace_session(self, token_hash: str, *, revoked_at: datetime) -> bool:
        row = self._session.exec(
            select(WorkspaceSession).where(
                WorkspaceSession.token_hash == token_hash,
                WorkspaceSession.revoked_at.is_(None),
            ).with_for_update()
        ).first()
        if row is None:
            return False
        row.revoked_at = revoked_at
        return True

    def create_user(self, user: WorkspaceUser) -> WorkspaceUser:
        self._session.add(user)
        self._session.flush()
        return user

    def create_membership(self, membership: TenantMembership) -> TenantMembership:
        self._session.add(membership)
        self._session.flush()
        return membership
