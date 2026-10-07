from pathlib import Path

from alembic import command
from alembic.config import Config


def test_initial_migration_renders_postgresql_schema_offline(capsys):
    repository_root = Path(__file__).resolve().parents[1]
    config = Config(str(repository_root / "alembic.ini"))

    command.upgrade(config, "head", sql=True)

    sql = capsys.readouterr().out.lower()
    for table_name in (
        "conversation_assignments",
        "workflow_executions",
        "follow_up_tasks",
        "api_keys",
        "api_key_grants",
        "appointments",
        "appointment_slots",
        "tenants",
        "businesses",
        "contacts",
        "conversations",
        "messages",
        "workflow_state_records",
        "event_logs",
        "escalation_tickets",
        "routing_rules",
        "services",
        "staff",
        "lead_sources",
        "leads",
        "lead_enrichments",
        "lead_scores",
        "lead_state_transitions",
        "lead_event_logs",
        "campaigns",
        "campaign_sequences",
        "campaign_steps",
        "campaign_enrollments",
        "campaign_step_runs",
        "campaign_event_logs",
        "contact_channel_consents",
        "campaign_replies",
        "outreach_messages",
        "crm_sync_records",
        "policy_profiles",
        "country_policies",
        "consent_records",
        "suppression_rules",
        "policy_decision_logs",
        "queue_task_records",
        "feedback_events",
        "optimization_metrics",
        "optimization_recommendations",
        "optimization_feature_flags",
        "optimization_audit_logs",
    ):
        assert f"create table {table_name}" in sql

    assert "jsonb" in sql
    assert "timestamp with time zone" in sql
    assert "fk_conversations_tenant_contact_business" in sql
    assert "uq_conversations_tenant_id" in sql
    assert "fk_api_key_grants_key" in sql
    assert "uq_api_keys_hash" in sql
    assert "uq_queue_tasks_idempotency" in sql
    assert "ix_queue_tasks_due" in sql
    assert "ck_conversations_status" in sql
    assert "fk_conversation_assignments_staff" in sql
    assert "alter table conversations add column status" in sql
    assert "assigned_staff_id" in sql
    assert "fk_escalation_tickets_assigned_staff" in sql
    assert '"allowed":true' in sql
    assert '"allowed"%(true)s' not in sql


def test_api_key_migration_downgrade_renders_in_dependency_order(capsys):
    repository_root = Path(__file__).resolve().parents[1]
    config = Config(str(repository_root / "alembic.ini"))
    command.downgrade(config, "0008_api_key_auth:0007_geo_policy_engine", sql=True)
    sql = capsys.readouterr().out.lower()
    assert sql.index("drop table api_key_grants") < sql.index("drop table api_keys")


def test_conversation_migration_downgrade_preserves_existing_conversations(capsys):
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.downgrade(config, "0009_conversation_management:0008_api_key_auth", sql=True)
    sql = capsys.readouterr().out.lower()
    assert "drop table conversation_assignments" in sql
    assert "alter table conversations drop column status" in sql
    assert "drop table conversations" not in sql


def test_provider_receipt_migration_adds_and_drops_receipt_columns(capsys):
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "0013_provider_delivery_receipts", sql=True)
    upgrade_sql = capsys.readouterr().out.lower()
    assert "add column provider_message_id" in upgrade_sql
    assert "add column provider_status" in upgrade_sql
    assert "uq_outreach_messages_provider_id" in upgrade_sql

    command.downgrade(config, "0013_provider_delivery_receipts:0012_crm_sync", sql=True)
    downgrade_sql = capsys.readouterr().out.lower()
    assert "drop column provider_message_id" in downgrade_sql
    assert "drop column provider_status" in downgrade_sql
