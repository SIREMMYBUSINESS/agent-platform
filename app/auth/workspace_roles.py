"""Map workspace roles to the API's existing explicit permission names."""

from __future__ import annotations

from app.auth.api_keys import ApiKeyVerifier
from app.auth.principal_resolver import PrincipalGrant, VerifiedCredential

WORKSPACE_ROLES = frozenset({"owner", "admin", "manager", "agent", "viewer"})

OWNER_PERMISSIONS = (
    "lead_reader", "lead_writer",
    "conversation_reader", "conversation_writer",
    "campaign_reader", "campaign_writer",
    "business_onboarder", "inbound_writer",
    "workflow_executor", "region_reader",
    "optimization_reader", "metrics_reader",
)

ROLE_PERMISSIONS = {
    "owner": OWNER_PERMISSIONS,
    "admin": OWNER_PERMISSIONS,
    "manager": (
        "lead_reader", "lead_writer",
        "conversation_reader", "conversation_writer",
        "campaign_reader", "campaign_writer",
        "business_onboarder", "workflow_executor",
    ),
    "agent": (
        "conversation_reader", "conversation_writer",
        "inbound_writer", "workflow_executor",
    ),
    "viewer": (
        "lead_reader", "conversation_reader", "campaign_reader",
    ),
}


def permissions_for_workspace_role(role: str) -> tuple[str, ...]:
    try:
        return ROLE_PERMISSIONS[role]
    except KeyError:
        return ()


class WorkspaceSessionCredentialVerifier:
    """Verify random opaque workspace sessions by their SHA-256 digest."""

    def __init__(self, repository) -> None:
        self._repository = repository

    def lookup_verified(self, credential: str) -> VerifiedCredential | None:
        import hashlib
        import re

        if not isinstance(credential, str) or re.fullmatch(r"ws_[A-Za-z0-9_-]{43}", credential) is None:
            return None
        digest = hashlib.sha256(credential.encode("utf-8")).hexdigest()
        result = self._repository.find_valid_session(digest)
        if result is None:
            return None
        session, user, _membership = result
        return VerifiedCredential(
            principal_id=f"workspace-user:{user.id}",
            tenant_id=session.tenant_id,
            auth_source="email_magic_link",
            issued_at=session.issued_at,
            expires_at=session.expires_at,
        )


class CompositeCredentialVerifier:
    """Preserve API keys while accepting verified passwordless workspace sessions."""

    def __init__(self, *, api_keys: ApiKeyVerifier, workspace_sessions: WorkspaceSessionCredentialVerifier):
        self._api_keys = api_keys
        self._workspace_sessions = workspace_sessions

    def lookup_verified(self, credential: str) -> VerifiedCredential | None:
        if isinstance(credential, str) and credential.startswith("ap_"):
            return self._api_keys.lookup_verified(credential)
        if isinstance(credential, str) and credential.startswith("ws_"):
            return self._workspace_sessions.lookup_verified(credential)
        return None


class CompositeGrantLookup:
    """Resolve API-key grants or current workspace membership permissions."""

    def __init__(self, *, api_key_grants, workspace_identity) -> None:
        self._api_key_grants = api_key_grants
        self._workspace_identity = workspace_identity

    def get(self, *, principal_id: str, tenant_id: str) -> PrincipalGrant | None:
        if principal_id.startswith("workspace-user:"):
            from uuid import UUID

            try:
                user_id = UUID(principal_id.removeprefix("workspace-user:"))
            except ValueError:
                return None
            membership = self._workspace_identity.get_membership_for_user_tenant(
                user_id=user_id, tenant_id=tenant_id,
            )
            if membership is None:
                return None
            roles = permissions_for_workspace_role(membership.role)
            if not roles:
                return None
            return PrincipalGrant(
                principal_id=principal_id,
                tenant_id=tenant_id,
                roles=roles,
                enabled=membership.status == "active",
            )
        return self._api_key_grants.get(principal_id=principal_id, tenant_id=tenant_id)
