from datetime import UTC
from uuid import UUID

from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import SQLModel

from app.app_db_models_Version2 import Tenant as Version2Tenant
from app.persistence.models import (
    Business,
    Contact,
    Conversation,
    EventLog,
    Message,
    Tenant,
    WorkflowStateRecord,
)


EXPECTED_TABLES = {
    "api_keys",
    "api_key_grants",
    "appointments",
    "appointment_slots",
    "tenants",
    "businesses",
    "contacts",
    "contact_channel_consents",
    "consent_records",
    "policy_profiles",
    "country_policies",
    "suppression_rules",
    "policy_decision_logs",
    "conversations",
    "messages",
    "workflow_state_records",
    "event_logs",
    "escalation_tickets",
    "routing_rules",
    "services",
    "staff",
    "leads",
    "lead_sources",
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
    "campaign_replies",
}


def test_all_persistence_models_share_one_metadata_registry():
    assert set(SQLModel.metadata.tables) == EXPECTED_TABLES
    assert Version2Tenant is Tenant
    assert Version2Tenant.metadata is SQLModel.metadata


def test_every_tenant_owned_table_carries_tenant_id():
    for table_name in EXPECTED_TABLES - {"tenants", "country_policies"}:
        assert "tenant_id" in SQLModel.metadata.tables[table_name].columns


def test_conversation_uses_generated_uuid_independent_of_contact_id():
    tenant_id = Tenant(id="tenant-1", name="Tenant").id
    business_id = Business(
        id="business-1", tenant_id=tenant_id, name="Business", vertical="general"
    ).id
    contact_id = Contact(id="contact-1", tenant_id=tenant_id, business_id=business_id).id
    first = Conversation(tenant_id=tenant_id, business_id=business_id, contact_id=contact_id, channel="chat")
    second = Conversation(tenant_id=tenant_id, business_id=business_id, contact_id=contact_id, channel="chat")

    assert isinstance(first.id, UUID)
    assert first.id != contact_id
    assert second.id != first.id
    assert first.contact_id == second.contact_id == contact_id


def test_provisioned_entity_ids_remain_compatible_with_string_api_ids():
    tenant = Tenant(id="tenant-1", name="Tenant")
    business = Business(id="business-1", tenant_id=tenant.id, name="Business", vertical="general")
    contact = Contact(id="contact-1", tenant_id=tenant.id, business_id=business.id)

    assert contact.tenant_id == "tenant-1"
    assert contact.business_id == "business-1"
    assert tenant.id == "tenant-1"


def test_tenant_consistency_is_encoded_as_composite_foreign_keys():
    expected_constraints = {
        "contacts": "fk_contacts_tenant_business",
        "conversations": {
            "fk_conversations_tenant_business",
            "fk_conversations_tenant_contact_business",
        },
        "messages": "fk_messages_tenant_conversation",
        "workflow_state_records": "fk_workflow_state_records_tenant_conversation",
        "event_logs": {
            "fk_event_logs_tenant_business",
            "fk_event_logs_tenant_conversation_business",
        },
    }

    for table_name, expected in expected_constraints.items():
        actual = {
            constraint.name
            for constraint in SQLModel.metadata.tables[table_name].foreign_key_constraints
        }
        expected_names = {expected} if isinstance(expected, str) else expected
        assert expected_names <= actual


def test_persistence_timestamps_are_timezone_aware():
    timestamp_columns = {
        "appointments": ("starts_at", "ends_at", "created_at", "updated_at"),
        "appointment_slots": ("starts_at", "ends_at", "created_at"),
        "tenants": ("created_at", "updated_at"),
        "businesses": ("created_at", "updated_at"),
        "contacts": ("created_at", "updated_at"),
        "contact_channel_consents": ("created_at", "updated_at"),
        "consent_records": ("expires_at", "created_at"),
        "policy_profiles": ("created_at", "updated_at"),
        "country_policies": ("updated_at",),
        "suppression_rules": ("created_at",),
        "policy_decision_logs": ("created_at",),
        "conversations": ("started_at", "updated_at"),
        "messages": ("created_at",),
        "workflow_state_records": ("created_at",),
        "event_logs": ("created_at",),
        "escalation_tickets": ("created_at",),
        "routing_rules": ("created_at",),
        "services": ("created_at",),
        "staff": ("created_at",),
        "lead_sources": ("created_at",),
        "leads": ("created_at", "updated_at"),
        "lead_enrichments": ("created_at",),
        "lead_scores": ("created_at",),
        "lead_state_transitions": ("created_at",),
        "lead_event_logs": ("created_at",),
        "campaigns": ("created_at", "updated_at"),
        "campaign_sequences": ("created_at",),
        "campaign_steps": ("created_at",),
        "campaign_enrollments": ("next_step_at", "enrolled_at", "updated_at"),
        "campaign_step_runs": ("generated_at", "sent_at"),
        "campaign_event_logs": ("created_at",),
        "campaign_replies": ("created_at",),
    }

    for table_name, column_names in timestamp_columns.items():
        for column_name in column_names:
            assert SQLModel.metadata.tables[table_name].c[column_name].type.timezone is True

    tenant = Tenant(name="Tenant")
    assert tenant.created_at.tzinfo == UTC


def test_json_fields_use_jsonb_on_postgresql_without_value_uniqueness_rules():
    context_type = WorkflowStateRecord.__table__.c.context.type.dialect_impl(postgresql.dialect())
    payload_type = EventLog.__table__.c.payload.type.dialect_impl(postgresql.dialect())
    assert isinstance(context_type, JSONB)
    assert isinstance(payload_type, JSONB)

    for table_name in ("contacts",):
        unique_columns = {
            column.name
            for constraint in SQLModel.metadata.tables[table_name].constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
            for column in constraint.columns
        }
        assert not {"full_name", "phone", "email", "whatsapp"} & unique_columns


def test_multiple_conversations_per_contact_are_allowed_by_schema():
    table = Conversation.__table__

    assert not any(
        "contact_id" in constraint.columns.keys()
        and constraint.__class__.__name__ == "UniqueConstraint"
        for constraint in table.constraints
    )
