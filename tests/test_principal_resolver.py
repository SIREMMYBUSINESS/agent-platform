from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from app.auth.principal_resolver import (
    AuthenticationError, AuthorizationError, PrincipalGrant, PrincipalResolver,
    VerifiedCredential,
)
from app.security import AuthenticatedPrincipal


NOW = datetime(2026, 1, 2, tzinfo=UTC)
IDENTITY = VerifiedCredential("integration-1", "tenant-1", "api_key", NOW - timedelta(days=1))
GRANT = PrincipalGrant("integration-1", "tenant-1", ("inbound_writer",))


@pytest.fixture
def dependencies():
    credentials = Mock(spec=["lookup_verified"])
    credentials.lookup_verified.return_value = IDENTITY
    tenants = Mock(spec=["exists"])
    tenants.exists.return_value = True
    grants = Mock(spec=["get"])
    grants.get.return_value = GRANT
    resolver = PrincipalResolver(
        credentials=credentials, tenants=tenants, grants=grants,
        now_provider=lambda: NOW,
    )
    return resolver, credentials, tenants, grants


def test_trusted_principal_resolves_from_verified_mapping_and_grant(dependencies):
    resolver, credentials, tenants, grants = dependencies
    result = resolver.resolve("test-only-credential")
    assert result == AuthenticatedPrincipal(
        "integration-1", "tenant-1", ("inbound_writer",), "api_key", NOW,
    )
    credentials.lookup_verified.assert_called_once_with("test-only-credential")
    tenants.exists.assert_called_once_with(tenant_id=result.tenant_id)
    grants.get.assert_called_once_with(principal_id=result.principal_id, tenant_id=result.tenant_id)


@pytest.mark.parametrize("credential", [None, "", " "])
def test_missing_credentials_denied_before_lookup(dependencies, credential):
    resolver, credentials, tenants, grants = dependencies
    with pytest.raises(AuthenticationError):
        resolver.resolve(credential)
    credentials.lookup_verified.assert_not_called()
    tenants.exists.assert_not_called()
    grants.get.assert_not_called()


@pytest.mark.parametrize("identity", [
    None, replace(IDENTITY, revoked=True),
    replace(IDENTITY, expires_at=NOW),
    replace(IDENTITY, expires_at=NOW - timedelta(seconds=1)),
    replace(IDENTITY, issued_at=NOW + timedelta(seconds=1)),
    replace(IDENTITY, issued_at=NOW.replace(tzinfo=None)),
    replace(IDENTITY, expires_at=NOW.replace(tzinfo=None)),
    replace(IDENTITY, tenant_id=""),
])
def test_invalid_or_expired_credentials_denied_before_tenant_access(dependencies, identity):
    resolver, credentials, tenants, grants = dependencies
    credentials.lookup_verified.return_value = identity
    with pytest.raises(AuthenticationError, match="^Invalid credential$"):
        resolver.resolve("test-only-invalid-key")
    tenants.exists.assert_not_called()
    grants.get.assert_not_called()


def test_unknown_tenant_denied_without_provisioning(dependencies):
    resolver, _, tenants, grants = dependencies
    tenants.exists.return_value = False
    with pytest.raises(AuthorizationError):
        resolver.resolve("test-only-credential")
    grants.get.assert_not_called()
    assert [call[0] for call in tenants.mock_calls] == ["exists"]


@pytest.mark.parametrize("grant", [
    None, replace(GRANT, enabled=False), replace(GRANT, roles=()),
    replace(GRANT, roles=["admin"]), replace(GRANT, roles=(" ",)),
    replace(GRANT, tenant_id="tenant-2"),
    replace(GRANT, principal_id="another-integration"),
    replace(GRANT, expires_at=NOW),
    replace(GRANT, expires_at=NOW - timedelta(seconds=1)),
    replace(GRANT, expires_at=NOW.replace(tzinfo=None)),
])
def test_missing_invalid_or_cross_tenant_grants_denied(dependencies, grant):
    resolver, _, _, grants = dependencies
    grants.get.return_value = grant
    with pytest.raises(AuthorizationError, match="^Tenant access denied$"):
        resolver.resolve("test-only-credential")


@pytest.mark.parametrize("credential_hours,grant_hours", [(1, 2), (2, 1)])
def test_principal_expires_at_earliest_credential_or_grant_bound(
    dependencies, credential_hours, grant_hours,
):
    resolver, credentials, _, grants = dependencies
    credentials.lookup_verified.return_value = replace(
        IDENTITY, expires_at=NOW + timedelta(hours=credential_hours),
    )
    grants.get.return_value = replace(GRANT, expires_at=NOW + timedelta(hours=grant_hours))
    assert resolver.resolve("test-only-credential").expires_at == NOW + timedelta(hours=1)


def test_request_tenant_cannot_be_passed_as_authority(dependencies):
    resolver, credentials, _, _ = dependencies
    with pytest.raises(TypeError):
        resolver.resolve("test-only-credential", tenant_id="attacker-tenant")
    credentials.lookup_verified.assert_not_called()


def test_lookup_failure_never_falls_back_to_a_principal(dependencies):
    resolver, credentials, _, _ = dependencies
    credentials.lookup_verified.side_effect = RuntimeError("lookup unavailable")
    with pytest.raises(RuntimeError, match="lookup unavailable"):
        resolver.resolve("test-only-credential")
