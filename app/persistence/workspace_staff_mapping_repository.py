"""Tenant/business-scoped persistence for workspace-to-staff mappings."""

from __future__ import annotations

from uuid import UUID

from sqlmodel import Session, select

from app.persistence.models import WorkspaceStaffMapping, WorkspaceStaffMappingEvent


class SQLWorkspaceStaffMappingRepository:
    """Repository uses the UnitOfWork session; it never commits independently."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_for_workspace_user(
        self, *, tenant_id: str, business_id: str, workspace_user_id: UUID,
    ) -> WorkspaceStaffMapping | None:
        statement = select(WorkspaceStaffMapping).where(
            WorkspaceStaffMapping.tenant_id == tenant_id,
            WorkspaceStaffMapping.business_id == business_id,
            WorkspaceStaffMapping.workspace_user_id == workspace_user_id,
        )
        return self._session.exec(statement).first()

    def get_by_id(
        self, *, tenant_id: str, business_id: str, mapping_id: UUID,
    ) -> WorkspaceStaffMapping | None:
        statement = select(WorkspaceStaffMapping).where(
            WorkspaceStaffMapping.tenant_id == tenant_id,
            WorkspaceStaffMapping.business_id == business_id,
            WorkspaceStaffMapping.id == mapping_id,
        )
        return self._session.exec(statement).first()

    def list_for_business(
        self, *, tenant_id: str, business_id: str,
    ) -> list[WorkspaceStaffMapping]:
        statement = (
            select(WorkspaceStaffMapping)
            .where(
                WorkspaceStaffMapping.tenant_id == tenant_id,
                WorkspaceStaffMapping.business_id == business_id,
            )
            .order_by(WorkspaceStaffMapping.created_at, WorkspaceStaffMapping.id)
        )
        return list(self._session.exec(statement).all())

    def create(self, mapping: WorkspaceStaffMapping) -> WorkspaceStaffMapping:
        self._session.add(mapping)
        self._session.flush()
        return mapping

    def add_event(self, event: WorkspaceStaffMappingEvent) -> WorkspaceStaffMappingEvent:
        self._session.add(event)
        self._session.flush()
        return event

    def delete(self, mapping: WorkspaceStaffMapping) -> None:
        self._session.delete(mapping)
        self._session.flush()
