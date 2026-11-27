"""Owner-managed workspace identity to business staff mappings."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.persistence.models import WorkspaceStaffMapping, WorkspaceStaffMappingEvent
from app.persistence.unit_of_work import UnitOfWork
from app.security import AuthenticatedPrincipal


WORKSPACE_PRINCIPAL_PREFIX = "workspace-user:"


class WorkspaceStaffMappingNotFound(Exception):
    """The requested business, active staff member, or mapping is unavailable."""


class WorkspaceStaffMappingConflict(Exception):
    """A workspace user or staff member already has a mapping in this business."""


def workspace_user_id_from_principal(principal: AuthenticatedPrincipal) -> UUID | None:
    """Resolve only verified passwordless workspace principals; API keys fail closed."""
    if (
        not isinstance(principal, AuthenticatedPrincipal)
        or principal.auth_source != "email_magic_link"
        or not principal.principal_id.startswith(WORKSPACE_PRINCIPAL_PREFIX)
    ):
        return None
    raw_id = principal.principal_id.removeprefix(WORKSPACE_PRINCIPAL_PREFIX)
    try:
        return UUID(raw_id)
    except (ValueError, TypeError, AttributeError):
        return None


def resolve_staff_id(
    uow: UnitOfWork,
    principal: AuthenticatedPrincipal,
    *,
    business_id: str,
) -> UUID | None:
    """Resolve the current principal's active staff record in one business.

    No email matching, cross-tenant fallback, or API-key-to-staff inference is
    permitted. A missing, malformed, or inactive mapping returns None.
    """
    workspace_user_id = workspace_user_id_from_principal(principal)
    if workspace_user_id is None or not business_id or not principal.tenant_id:
        return None
    mapping = uow.workspace_staff_mappings.get_for_workspace_user(
        tenant_id=principal.tenant_id,
        business_id=business_id,
        workspace_user_id=workspace_user_id,
    )
    if mapping is None:
        return None
    staff = uow.staff.get_active(
        tenant_id=principal.tenant_id,
        business_id=business_id,
        staff_id=mapping.staff_id,
    )
    return staff.id if staff is not None else None


def _mapping_view(row: WorkspaceStaffMapping) -> dict:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "business_id": row.business_id,
        "workspace_user_id": row.workspace_user_id,
        "staff_id": row.staff_id,
        "created_at": row.created_at,
    }


class WorkspaceStaffMappingService:
    """Mutations are atomic with their append-only audit events."""

    def __init__(self, *, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow_factory = uow_factory

    def list_for_business(self, *, tenant_id: str, business_id: str) -> list[dict]:
        with self._uow_factory() as uow:
            if uow.businesses.get(tenant_id=tenant_id, business_id=business_id) is None:
                raise WorkspaceStaffMappingNotFound
            return [
                _mapping_view(row)
                for row in uow.workspace_staff_mappings.list_for_business(
                    tenant_id=tenant_id, business_id=business_id,
                )
            ]

    def create(
        self, *, tenant_id: str, business_id: str,
        workspace_user_id: UUID, staff_id: UUID, actor_workspace_user_id: UUID,
    ) -> dict:
        try:
            with self._uow_factory() as uow:
                if uow.businesses.get(tenant_id=tenant_id, business_id=business_id) is None:
                    raise WorkspaceStaffMappingNotFound
                staff = uow.staff.get_active(
                    tenant_id=tenant_id, business_id=business_id, staff_id=staff_id,
                )
                if staff is None:
                    raise WorkspaceStaffMappingNotFound
                row = WorkspaceStaffMapping(
                    tenant_id=tenant_id, business_id=business_id,
                    workspace_user_id=workspace_user_id, staff_id=staff_id,
                )
                uow.workspace_staff_mappings.create(row)
                uow.workspace_staff_mappings.add_event(WorkspaceStaffMappingEvent(
                    tenant_id=tenant_id,
                    business_id=business_id,
                    mapping_id=row.id,
                    workspace_user_id=workspace_user_id,
                    staff_id=staff_id,
                    actor_workspace_user_id=actor_workspace_user_id,
                    event_type="created",
                ))
                return _mapping_view(row)
        except IntegrityError:
            raise WorkspaceStaffMappingConflict(
                "A workspace user or staff member is already mapped in this business"
            ) from None

    def remove(
        self, *, tenant_id: str, business_id: str,
        mapping_id: UUID, actor_workspace_user_id: UUID,
    ) -> None:
        with self._uow_factory() as uow:
            row = uow.workspace_staff_mappings.get_by_id(
                tenant_id=tenant_id, business_id=business_id, mapping_id=mapping_id,
            )
            if row is None:
                raise WorkspaceStaffMappingNotFound
            uow.workspace_staff_mappings.add_event(WorkspaceStaffMappingEvent(
                tenant_id=tenant_id,
                business_id=business_id,
                mapping_id=row.id,
                workspace_user_id=row.workspace_user_id,
                staff_id=row.staff_id,
                actor_workspace_user_id=actor_workspace_user_id,
                event_type="removed",
            ))
            uow.workspace_staff_mappings.delete(row)
