"""Provisioning tool for the voice number directory: validated, tenant-safe, no silent moves."""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.persistence.models import Business, Tenant, VoiceNumberRoute
from app.persistence.unit_of_work import UnitOfWork
from scripts.provision_voice_number import main

NUMBER = "+15550001111"


@pytest.fixture
def env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([Tenant(id="tenant-a", name="A"), Tenant(id="tenant-b", name="B")])
        session.flush()
        session.add_all([
            Business(id="biz-a", tenant_id="tenant-a", name="A", vertical="general"),
            Business(id="biz-b", tenant_id="tenant-b", name="B", vertical="general"),
        ])
        session.commit()
    factory = lambda: UnitOfWork(lambda: Session(engine))

    def run(*args):
        return main(list(args), directory_uow_factory=factory, business_uow_factory=factory)

    def row(number=NUMBER):
        with Session(engine) as session:
            return session.get(VoiceNumberRoute, number)

    yield run, row
    engine.dispose()


def base(number=NUMBER, tenant="tenant-a", business="biz-a"):
    return ["--number", number, "--tenant-id", tenant, "--business-id", business]


def test_creates_a_route_for_an_existing_business(env, capsys):
    run, row = env
    assert run(*base()) == 0
    assert capsys.readouterr().out.strip() == f"created: {NUMBER} -> business biz-a"
    stored = row()
    assert (stored.tenant_id, stored.business_id, stored.status) == ("tenant-a", "biz-a", "active")


def test_rerunning_the_same_request_is_unchanged(env, capsys):
    run, _ = env
    run(*base())
    capsys.readouterr()
    assert run(*base()) == 0
    assert capsys.readouterr().out.startswith("unchanged:")


def test_disable_and_reenable(env, capsys):
    run, row = env
    run(*base())
    assert run(*base(), "--disable") == 0 and row().status == "disabled"
    assert run(*base()) == 0 and row().status == "active"
    out = capsys.readouterr().out
    assert "disabled:" in out and "enabled:" in out


@pytest.mark.parametrize("business,tenant", [("ghost", "tenant-a"), ("biz-b", "tenant-a"), ("biz-a", "tenant-b")])
def test_business_must_exist_under_that_tenant(env, capsys, business, tenant):
    run, row = env
    assert run(*base(tenant=tenant, business=business)) == 2
    assert "business not found under that tenant" in capsys.readouterr().err
    assert row() is None


def test_a_number_is_never_moved_between_businesses_by_accident(env, capsys):
    run, row = env
    run(*base())
    capsys.readouterr()
    assert run(*base(tenant="tenant-b", business="biz-b")) == 2
    err = capsys.readouterr().err
    assert "--reassign" in err
    assert "tenant-a" not in err and "biz-a" not in err  # no cross-tenant identifiers in messages
    assert (row().tenant_id, row().business_id) == ("tenant-a", "biz-a")


def test_reassign_moves_the_number_when_explicit(env, capsys):
    run, row = env
    run(*base())
    assert run(*base(tenant="tenant-b", business="biz-b"), "--reassign") == 0
    assert (row().tenant_id, row().business_id) == ("tenant-b", "biz-b")
    assert "reassigned:" in capsys.readouterr().out


@pytest.mark.parametrize("number", ["15550001111", "+0123456789", "+1 555", "+1555000111122223", "abc"])
def test_rejects_non_e164_numbers(env, capsys, number):
    run, row = env
    assert run(*base(number=number)) == 2
    assert "E.164" in capsys.readouterr().err


@pytest.mark.parametrize("tenant,business", [("a" * 65, "biz-a"), ("tenant.a", "biz-a"), ("tenant-a", "biz/a"), ("tenant-a", "")])
def test_rejects_ids_the_gateway_could_not_carry(env, capsys, tenant, business):
    run, _ = env
    assert run(*base(tenant=tenant, business=business)) == 2
    assert "ids must be" in capsys.readouterr().err
