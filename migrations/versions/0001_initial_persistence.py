"""Create initial tenant-scoped persistence schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("region", sa.String(), server_default="US", nullable=False),
        sa.Column("default_timezone", sa.String(), server_default="UTC", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_tenants"),
    )

    op.create_table(
        "businesses",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("vertical", sa.String(), nullable=False),
        sa.Column("region", sa.String(), server_default="US", nullable=False),
        sa.Column("default_language", sa.String(), server_default="en", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_businesses_tenant_id_tenants"),
        sa.PrimaryKeyConstraint("id", name="pk_businesses"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_businesses_tenant_id"),
    )

    op.create_table(
        "contacts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("full_name", sa.String(), nullable=True),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("whatsapp", sa.String(), nullable=True),
        sa.Column("consent_status", sa.String(), server_default="unknown", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_contacts_tenant_id_tenants"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "business_id"],
            ["businesses.tenant_id", "businesses.id"],
            name="fk_contacts_tenant_business",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_contacts"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_contacts_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "id", "business_id", name="uq_contacts_tenant_id_business_id"
        ),
    )

    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("contact_id", sa.String(), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("workflow_state", sa.String(), server_default="new", nullable=False),
        sa.Column("correlation_id", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_conversations_tenant_id_tenants"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "business_id"],
            ["businesses.tenant_id", "businesses.id"],
            name="fk_conversations_tenant_business",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contact_id", "business_id"],
            ["contacts.tenant_id", "contacts.id", "contacts.business_id"],
            name="fk_conversations_tenant_contact_business",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_conversations"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_conversations_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "id", "business_id", name="uq_conversations_tenant_id_business_id"
        ),
    )

    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sender", sa.String(), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_messages_tenant_id_tenants"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["conversations.tenant_id", "conversations.id"],
            name="fk_messages_tenant_conversation",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_messages"),
    )

    op.create_table(
        "workflow_state_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column(
            "context",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_workflow_state_records_tenant_id_tenants"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["conversations.tenant_id", "conversations.id"],
            name="fk_workflow_state_records_tenant_conversation",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_state_records"),
    )

    op.create_table(
        "event_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("correlation_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_event_logs_tenant_id_tenants"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "business_id"],
            ["businesses.tenant_id", "businesses.id"],
            name="fk_event_logs_tenant_business",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id", "business_id"],
            ["conversations.tenant_id", "conversations.id", "conversations.business_id"],
            name="fk_event_logs_tenant_conversation_business",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_event_logs"),
    )

    op.create_index("ix_businesses_tenant_id", "businesses", ["tenant_id", "id"])
    op.create_index("ix_contacts_tenant_business", "contacts", ["tenant_id", "business_id"])
    op.create_index("ix_conversations_tenant_business", "conversations", ["tenant_id", "business_id"])
    op.create_index("ix_conversations_tenant_contact", "conversations", ["tenant_id", "contact_id"])
    op.create_index("ix_conversations_correlation_id", "conversations", ["correlation_id"])
    op.create_index(
        "ix_messages_tenant_conversation_created",
        "messages",
        ["tenant_id", "conversation_id", "created_at"],
    )
    op.create_index(
        "ix_workflow_state_records_tenant_conversation_created",
        "workflow_state_records",
        ["tenant_id", "conversation_id", "created_at"],
    )
    op.create_index(
        "ix_event_logs_tenant_conversation_created",
        "event_logs",
        ["tenant_id", "conversation_id", "created_at"],
    )
    op.create_index(
        "ix_event_logs_tenant_event_type_created",
        "event_logs",
        ["tenant_id", "event_type", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_event_logs_tenant_event_type_created", table_name="event_logs")
    op.drop_index("ix_event_logs_tenant_conversation_created", table_name="event_logs")
    op.drop_index(
        "ix_workflow_state_records_tenant_conversation_created",
        table_name="workflow_state_records",
    )
    op.drop_index("ix_messages_tenant_conversation_created", table_name="messages")
    op.drop_index("ix_conversations_correlation_id", table_name="conversations")
    op.drop_index("ix_conversations_tenant_contact", table_name="conversations")
    op.drop_index("ix_conversations_tenant_business", table_name="conversations")
    op.drop_index("ix_contacts_tenant_business", table_name="contacts")
    op.drop_index("ix_businesses_tenant_id", table_name="businesses")
    op.drop_table("event_logs")
    op.drop_table("workflow_state_records")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("contacts")
    op.drop_table("businesses")
    op.drop_table("tenants")
