from pathlib import Path

from alembic import command
from alembic.config import Config


def test_initial_migration_renders_postgresql_schema_offline(capsys):
    repository_root = Path(__file__).resolve().parents[1]
    config = Config(str(repository_root / "alembic.ini"))

    command.upgrade(config, "head", sql=True)

    sql = capsys.readouterr().out.lower()
    for table_name in (
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
        "policy_profiles",
        "country_policies",
        "consent_records",
        "suppression_rules",
        "policy_decision_logs",
    ):
        assert f"create table {table_name}" in sql

    assert "jsonb" in sql
    assert "timestamp with time zone" in sql
    assert "fk_conversations_tenant_contact_business" in sql
    assert "uq_conversations_tenant_id" in sql
