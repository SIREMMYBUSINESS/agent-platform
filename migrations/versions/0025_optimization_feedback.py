"""Durable tenant-scoped optimization feedback, recommendations, flags, and audit."""
from alembic import op
import sqlalchemy as sa


revision = "0025_optimization_feedback"
down_revision = "0024_outreach_dry_run_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("campaign_enrollments", sa.Column(
        "experiment_group", sa.String(length=32), nullable=False,
        server_default="all",
    ))
    op.create_table(
        "feedback_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("event_key", sa.String(length=200), nullable=False),
        sa.Column("correlation_id", sa.String(length=100), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=True),
        sa.Column("contact_id", sa.String(), nullable=True),
        sa.Column("campaign_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.JSON(), nullable=False),
        sa.Column("outcome", sa.String(length=64), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_feedback_events_tenant"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"], name="fk_feedback_events_business"),
        sa.PrimaryKeyConstraint("id", name="pk_feedback_events"),
        sa.UniqueConstraint("tenant_id", "business_id", "event_key", name="uq_feedback_events_event_key"),
    )
    op.create_index("ix_feedback_events_scope_time", "feedback_events", ["tenant_id", "business_id", "occurred_at"])
    op.create_index("ix_feedback_events_scope_type", "feedback_events", ["tenant_id", "business_id", "event_type", "occurred_at"])

    op.create_table(
        "optimization_metrics",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("metric_key", sa.String(length=200), nullable=False),
        sa.Column("metric_type", sa.String(length=64), nullable=False),
        sa.Column("segment", sa.JSON(), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("metric_values", sa.JSON(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_optimization_metrics_tenant"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"], name="fk_optimization_metrics_business"),
        sa.PrimaryKeyConstraint("id", name="pk_optimization_metrics"),
        sa.UniqueConstraint("tenant_id", "business_id", "metric_key", "period_start", name="uq_optimization_metrics_period"),
    )
    op.create_index("ix_optimization_metrics_scope_type", "optimization_metrics", ["tenant_id", "business_id", "metric_type", "period_start"])

    op.create_table(
        "optimization_recommendations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("recommendation_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("change", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("expected_impact", sa.Float(), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("rationale", sa.String(length=2000), nullable=False),
        sa.Column("reviewed_by", sa.String(length=160), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_optimization_recommendation_confidence"),
        sa.CheckConstraint("sample_size >= 0", name="ck_optimization_recommendation_sample_size"),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected', 'applied', 'rolled_back')", name="ck_optimization_recommendation_status"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_optimization_recommendations_tenant"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"], name="fk_optimization_recommendations_business"),
        sa.PrimaryKeyConstraint("id", name="pk_optimization_recommendations"),
    )
    op.create_index("ix_optimization_recommendations_scope_status", "optimization_recommendations", ["tenant_id", "business_id", "status", "created_at"])

    op.create_table(
        "optimization_feature_flags",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("flag_key", sa.String(length=100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("rollout_percentage", sa.Integer(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("rollout_percentage >= 0 AND rollout_percentage <= 100", name="ck_optimization_feature_flag_rollout"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_optimization_feature_flags_tenant"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"], name="fk_optimization_feature_flags_business"),
        sa.PrimaryKeyConstraint("id", name="pk_optimization_feature_flags"),
        sa.UniqueConstraint("tenant_id", "business_id", "flag_key", name="uq_optimization_feature_flag_scope"),
    )

    op.create_table(
        "optimization_audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("recommendation_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=160), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_optimization_audit_tenant"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"], name="fk_optimization_audit_business"),
        sa.PrimaryKeyConstraint("id", name="pk_optimization_audit_logs"),
    )
    op.create_index("ix_optimization_audit_scope_created", "optimization_audit_logs", ["tenant_id", "business_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_optimization_audit_scope_created", table_name="optimization_audit_logs")
    op.drop_table("optimization_audit_logs")
    op.drop_table("optimization_feature_flags")
    op.drop_index("ix_optimization_recommendations_scope_status", table_name="optimization_recommendations")
    op.drop_table("optimization_recommendations")
    op.drop_index("ix_optimization_metrics_scope_type", table_name="optimization_metrics")
    op.drop_table("optimization_metrics")
    op.drop_index("ix_feedback_events_scope_type", table_name="feedback_events")
    op.drop_index("ix_feedback_events_scope_time", table_name="feedback_events")
    op.drop_table("feedback_events")
    op.drop_column("campaign_enrollments", "experiment_group")
