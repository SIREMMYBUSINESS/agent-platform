from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from app.security import AuthenticatedPrincipal


def principal(**overrides):
    values = dict(
        principal_id="integration-1",
        tenant_id="tenant-1",
        roles=("inbound_writer",),
        auth_source="api_key",
        issued_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    return AuthenticatedPrincipal(**(values | overrides))


@pytest.mark.parametrize("source", ["api_key", "jwt", "internal_worker", "crm_webhook"])
def test_principal_supports_provider_independent_identity(source):
    identity = principal(auth_source=source)
    assert identity.auth_source == source
    assert identity.tenant_id == "tenant-1"
    assert identity.expires_at is None


@pytest.mark.parametrize("field,value", [
    ("tenant_id", "another-tenant"),
    ("roles", ("admin",)),
])
def test_principal_cannot_be_reassigned(field, value):
    identity = principal()
    with pytest.raises(FrozenInstanceError):
        setattr(identity, field, value)


@pytest.mark.parametrize("field,value", [
    ("principal_id", ""), ("tenant_id", " "), ("auth_source", None),
    ("roles", ["admin"]), ("roles", ("",)), ("roles", (1,)),
    ("issued_at", datetime(2026, 1, 1)), ("issued_at", None),
    ("expires_at", datetime(2026, 1, 2)),
])
def test_principal_rejects_invalid_identity_shape(field, value):
    with pytest.raises(ValueError, match=field):
        principal(**{field: value})


@pytest.mark.parametrize("delta", [timedelta(0), timedelta(seconds=-1)])
def test_principal_rejects_expiration_at_or_before_issue(delta):
    issued = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="expires_at must be after issued_at"):
        principal(issued_at=issued, expires_at=issued + delta)


def test_principal_allows_no_roles_and_explicit_validity_bound():
    expiry = datetime(2026, 1, 2, tzinfo=UTC)
    identity = principal(roles=(), expires_at=expiry)
    assert identity.roles == ()
    assert identity.expires_at == expiry
