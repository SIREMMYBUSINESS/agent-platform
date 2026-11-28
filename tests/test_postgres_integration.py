"""Opt-in PostgreSQL migration and persisted inbound verification.

Set POSTGRES_TEST_DATABASE_URL to a dedicated database ending in ``_test`` to
run this test. It creates and drops only a temporary schema in that database.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.pool import NullPool
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.persistence.models import (
    Appointment,
    Business,
    ConsentRecord,
    ConversationAssignment,
    Contact,
    Conversation,
    EventLog,
    QueueTaskRecord,
    Staff,
    WorkspaceStaffMapping,
    Message,
    RoutingRule,
    Tenant,
    WorkflowStateRecord,
)
from app.persistence.unit_of_work import UnitOfWork
from app.schemas import InboundRequest
from app.services.inbound import InboundService
from app.auth.dependencies import get_authenticated_principal
from app.queue.contracts import QueueDomain
from app.security import AuthenticatedPrincipal
from app.services.outbound_delivery_service import OutboundDeliveryService
from app.main import app


@pytest.fixture
def migrated_postgres_schema():
    database_url = os.environ.get("POSTGRES_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is not configured")
    parsed = urlsplit(database_url)
    database_name = parsed.path.rstrip("/").split("/")[-1]
    if not parsed.scheme.startswith("postgresql") or not database_name.endswith("_test"):
        pytest.fail("POSTGRES_TEST_DATABASE_URL must target a PostgreSQL database ending in _test")

    schema_name = f"phase1e_{uuid4().hex}"
    admin_engine = create_engine(database_url, poolclass=NullPool)
    admin_connection = admin_engine.connect()
    target_engine = None
    try:
        admin_connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
        admin_connection.commit()
        target_engine = create_engine(
            database_url,
            connect_args={"options": f"-csearch_path={schema_name}"},
            poolclass=NullPool,
        )
        config_path = Path(__file__).resolve().parents[1] / "alembic.ini"
        alembic_config = Config(str(config_path))

        with target_engine.connect() as connection:
            alembic_config.attributes["connection"] = connection
            command.upgrade(alembic_config, "head")
            assert "appointments" in inspect(connection).get_table_names()
            command.downgrade(alembic_config, "0001_initial")
            assert "appointments" not in inspect(connection).get_table_names()
            command.upgrade(alembic_config, "head")

        yield target_engine
    finally:
        if target_engine is not None:
            target_engine.dispose()
        admin_connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_connection.commit()
        admin_connection.close()
        admin_engine.dispose()


@pytest.mark.postgres
def test_postgres_migrations_and_inbound_persistence(migrated_postgres_schema):
    engine = migrated_postgres_schema
    table_names = set(inspect(engine).get_table_names())
    assert {
        "tenants",
        "businesses",
        "contacts",
        "conversations",
        "messages",
        "appointments",
        "workflow_state_records",
        "event_logs",
    } <= table_names
    outreach_columns = {column["name"] for column in inspect(engine).get_columns("outreach_messages")}
    assert {"provider_message_id", "provider_status", "provider_error_code"} <= outreach_columns
    outreach_constraints = inspect(engine).get_unique_constraints("outreach_messages")
    assert any(item["name"] == "uq_outreach_messages_provider_id" for item in outreach_constraints)

    tenant_id = f"tenant-{uuid4()}"
    business_id = f"business-{uuid4()}"
    contact_id = f"contact-{uuid4()}"
    with Session(engine) as session:
        session.add(Tenant(id=tenant_id, name="Integration tenant"))
        session.flush()
        session.add(Business(
            id=business_id, tenant_id=tenant_id, name="Integration business",
            vertical="general", region="CA",
        ))
        session.flush()
        session.add_all([
            RoutingRule(
                tenant_id=tenant_id, business_id=business_id, weekday=weekday,
                opens_at=time(0, 0), closes_at=time(23, 59),
            )
            for weekday in range(7)
        ])
        session.add(Contact(
            id=contact_id, tenant_id=tenant_id, business_id=business_id,
            full_name="Integration contact",
        ))
        session.flush()
        session.add(ConsentRecord(
            tenant_id=tenant_id, business_id=business_id, contact_id=contact_id,
            channel="email", status="granted", source="trusted_integration_fixture",
        ))
        session.commit()

    service = InboundService(
        trusted_tenant_id=tenant_id,
        uow_factory=lambda: UnitOfWork(lambda: Session(engine)),
    )
    outcome = service.handle(InboundRequest(
        tenant_id=tenant_id,
        business_id=business_id,
        contact_id=contact_id,
        channel="email",
        text="What are your business hours?",
        contact_name="Integration contact",
        consent_status="granted",
        metadata={"region": "US"},
    ))

    assert outcome["success"] is True
    with Session(engine) as session:
        conversations = session.exec(select(Conversation)).all()
        messages = session.exec(select(Message)).all()
        events = session.exec(select(EventLog)).all()
        workflows = session.exec(select(WorkflowStateRecord)).all()
        persisted_business = session.get(Business, business_id)
        assert session.exec(select(Appointment)).all() == []
        committed_event_count = len(events)
        committed_workflow_count = len(workflows)

    assert len(conversations) == 1
    assert conversations[0].tenant_id == tenant_id
    assert conversations[0].workflow_state == "business_hours_checked"
    assert len(messages) == 2
    assert {event.event_type for event in events} >= {
        "intake.received",
        "business.hours.checked",
    }
    assert persisted_business is not None and persisted_business.region == "CA"
    assert {workflow.state for workflow in workflows} == {
        "intake_received",
        "intent_classified",
        "business_hours_checked",
    }

    class FailingRoutingService:
        def evaluate(self, **_kwargs):
            raise RuntimeError("integration routing failure")

    failing_service = InboundService(
        trusted_tenant_id=tenant_id,
        uow_factory=lambda: UnitOfWork(lambda: Session(engine)),
        routing_service=FailingRoutingService(),
    )
    with pytest.raises(RuntimeError, match="integration routing failure"):
        failing_service.handle(InboundRequest(
            tenant_id=tenant_id,
            business_id=business_id,
            contact_id=contact_id,
            channel="email",
            text="This transaction must roll back",
            consent_status="granted",
        ))

    with Session(engine) as session:
        assert len(session.exec(select(Conversation)).all()) == 1
        assert len(session.exec(select(Message)).all()) == 2
        assert len(session.exec(select(EventLog)).all()) == committed_event_count
        assert len(session.exec(select(WorkflowStateRecord)).all()) == committed_workflow_count


@pytest.mark.postgres
def test_postgres_composer_rolls_back_flushed_message_and_queue_on_integrity_error(
    migrated_postgres_schema, monkeypatch,
):
    """Exercise the route's duplicate-collision rollback against real PostgreSQL."""
    from app.routes import conversation_sms as sms_route

    engine = migrated_postgres_schema
    now = datetime(2026, 11, 27, 10, 0, tzinfo=UTC)
    tenant_id = f"tenant-{uuid4()}"
    business_id = f"business-{uuid4()}"
    contact_id = f"contact-{uuid4()}"
    conversation_id = uuid4()
    workspace_user_id = uuid4()
    staff_id = uuid4()

    with Session(engine) as session:
        session.add(Tenant(id=tenant_id, name="Composer rollback tenant", region="US"))
        session.flush()
        session.add(Business(
            id=business_id, tenant_id=tenant_id, name="Composer rollback business",
            vertical="general", region="US", country_code="US", timezone="UTC",
        ))
        session.flush()
        session.add(Contact(
            id=contact_id, tenant_id=tenant_id, business_id=business_id,
            phone="+15551234567",
        ))
        session.add(Staff(
            id=staff_id, tenant_id=tenant_id, business_id=business_id,
            display_name="Integration staff",
        ))
        session.flush()
        session.add(Conversation(
            id=conversation_id, tenant_id=tenant_id, business_id=business_id,
            contact_id=contact_id, channel="sms",
        ))
        session.flush()
        session.add_all([
            WorkspaceStaffMapping(
                tenant_id=tenant_id, business_id=business_id,
                workspace_user_id=workspace_user_id, staff_id=staff_id,
            ),
            ConversationAssignment(
                tenant_id=tenant_id, business_id=business_id,
                conversation_id=conversation_id, staff_id=staff_id,
                assigned_by="postgres-integration-test",
            ),
            *[
                RoutingRule(
                    tenant_id=tenant_id, business_id=business_id, weekday=day,
                    opens_at=time(0, 0), closes_at=time(23, 59, 59),
                    after_hours_action="callback", active=True,
                )
                for day in range(7)
            ],
            ConsentRecord(
                tenant_id=tenant_id, business_id=business_id,
                contact_id=contact_id, channel="sms", status="granted", source="test",
            ),
        ])
        session.commit()

    principal = AuthenticatedPrincipal(
        principal_id=f"workspace-user:{workspace_user_id}",
        tenant_id=tenant_id,
        roles=("conversation_writer",),
        auth_source="email_magic_link",
        issued_at=now - timedelta(minutes=1),
    )
    monkeypatch.setattr(sms_route.settings, "environment", "staging")
    monkeypatch.setattr(sms_route.settings, "conversation_sms_composer_enabled", True)
    monkeypatch.setattr(sms_route, "session_factory", lambda: Session(engine))
    monkeypatch.setattr(sms_route, "utcnow", lambda: now)
    app.dependency_overrides[get_authenticated_principal] = lambda: principal

    def stage_then_violate_unique_constraint(
        self, uow, *, tenant, business, contact, conversation, channel, content,
        message_id=None, correlation_id=None, subject=None,
        campaign_step_run_id=None, sent_by_staff_id=None,
    ):
        uow.messages.add(
            tenant_id=tenant.id, conversation_id=conversation.id, sender="agent",
            channel=channel, content=content, direction="outbound",
            delivery_status="pending", message_id=message_id, subject=subject,
            sent_by_staff_id=sent_by_staff_id,
        )
        uow.queue_tasks.enqueue(
            tenant_id=tenant.id, business_id=business.id,
            domain=QueueDomain.OUTREACH.value, task_name="communication.deliver_message",
            idempotency_key=f"message:{message_id}:delivery",
            aggregate_type="message", aggregate_id=str(message_id),
            payload={"message_id": str(message_id)},
            correlation_id=correlation_id or str(message_id),
            traceparent=None, available_at=now, max_attempts=6,
        )
        # First flush proves both intended writes reached PostgreSQL. The second
        # INSERT violates the real messages primary key and aborts the transaction.
        uow.session.flush()
        uow.session.execute(
            text("""
                INSERT INTO messages
                    (id, tenant_id, conversation_id, sender, channel, content,
                     direction, delivery_status)
                VALUES
                    (:id, :tenant_id, :conversation_id, 'agent', 'sms',
                     'duplicate', 'outbound', 'pending')
            """),
            {
                "id": str(message_id),
                "tenant_id": tenant.id,
                "conversation_id": str(conversation.id),
            },
        )
        uow.session.flush()

    monkeypatch.setattr(
        OutboundDeliveryService, "queue", stage_then_violate_unique_constraint,
    )
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                f"/businesses/{business_id}/conversations/{conversation_id}/sms",
                json={"content": "Rollback integration test", "client_key": str(uuid4())},
            )
        assert response.status_code == 409
        assert response.json()["reason_code"] == "idempotency_key_in_progress"

        # Independent sessions ensure no uncommitted/identity-map state masks rows.
        with Session(engine) as session:
            assert session.scalars(
                select(Message).where(Message.tenant_id == tenant_id)
            ).all() == []
            assert session.scalars(
                select(QueueTaskRecord).where(QueueTaskRecord.tenant_id == tenant_id)
            ).all() == []
            assert session.scalars(
                select(EventLog).where(
                    EventLog.tenant_id == tenant_id,
                    EventLog.source == "conversation_sms_composer",
                )
            ).all() == []
    finally:
        app.dependency_overrides.clear()
