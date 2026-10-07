"""Persistence abstractions for feedback, optimizer state, and audit history."""
from datetime import UTC, datetime
from uuid import UUID

from sqlmodel import Session, select

from app.persistence.models import (
    CampaignEventLog, FeedbackEvent, FeatureFlag, OptimizationAuditLog,
    OptimizationMetric, OptimizationRecommendation, ProviderWebhookEvent,
)


class SQLOptimizationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_feedback(self, **values) -> FeedbackEvent:
        existing = self._session.exec(select(FeedbackEvent).where(
            FeedbackEvent.tenant_id == values["tenant_id"],
            FeedbackEvent.business_id == values["business_id"],
            FeedbackEvent.event_key == values["event_key"],
        )).first()
        if existing is not None:
            if (existing.event_type != values["event_type"] or
                    existing.outcome != values["outcome"] or
                    existing.correlation_id != values["correlation_id"]):
                raise ValueError("feedback idempotency key conflicts with different event")
            return existing
        row = FeedbackEvent(**values)
        self._session.add(row)
        self._session.flush()
        return row

    def feedback(self, *, tenant_id: str, business_id: str,
                 since: datetime | None = None, until: datetime | None = None,
                 event_type: str | None = None, limit: int = 10000) -> list[FeedbackEvent]:
        statement = select(FeedbackEvent).where(
            FeedbackEvent.tenant_id == tenant_id,
            FeedbackEvent.business_id == business_id,
        )
        if since is not None:
            statement = statement.where(FeedbackEvent.occurred_at >= since)
        if until is not None:
            statement = statement.where(FeedbackEvent.occurred_at < until)
        if event_type is not None:
            statement = statement.where(FeedbackEvent.event_type == event_type)
        return list(self._session.exec(statement.order_by(FeedbackEvent.occurred_at).limit(limit)).all())

    def campaign_events(self, *, tenant_id: str, business_id: str,
                        since: datetime | None = None, limit: int = 20000):
        statement = select(CampaignEventLog).where(
            CampaignEventLog.tenant_id == tenant_id,
            CampaignEventLog.business_id == business_id,
        )
        if since is not None:
            statement = statement.where(CampaignEventLog.created_at >= since)
        return list(self._session.exec(statement.order_by(
            CampaignEventLog.created_at).limit(limit)).all())

    def provider_events(self, *, tenant_id: str, business_id: str,
                        since: datetime | None = None, limit: int = 20000):
        statement = select(ProviderWebhookEvent).where(
            ProviderWebhookEvent.tenant_id == tenant_id,
            ProviderWebhookEvent.business_id == business_id,
            ProviderWebhookEvent.provider == "sendgrid",
            ProviderWebhookEvent.event_type == "email.events",
        )
        if since is not None:
            statement = statement.where(ProviderWebhookEvent.created_at >= since)
        return list(self._session.exec(statement.order_by(
            ProviderWebhookEvent.created_at).limit(limit)).all())

    def add_recommendation(self, **values) -> OptimizationRecommendation:
        row = OptimizationRecommendation(**values)
        self._session.add(row)
        self._session.flush()
        return row

    def recommendation(self, *, tenant_id: str, business_id: str,
                        recommendation_id: UUID) -> OptimizationRecommendation | None:
        return self._session.exec(select(OptimizationRecommendation).where(
            OptimizationRecommendation.id == recommendation_id,
            OptimizationRecommendation.tenant_id == tenant_id,
            OptimizationRecommendation.business_id == business_id,
        )).first()

    def recommendations(self, *, tenant_id: str, business_id: str,
                        status: str | None = "pending", limit: int = 100):
        statement = select(OptimizationRecommendation).where(
            OptimizationRecommendation.tenant_id == tenant_id,
            OptimizationRecommendation.business_id == business_id,
        )
        if status is not None:
            statement = statement.where(OptimizationRecommendation.status == status)
        return list(self._session.exec(statement.order_by(
            OptimizationRecommendation.created_at.desc()).limit(limit)).all())

    def feature_flag(self, *, tenant_id: str, business_id: str,
                     flag_key: str) -> FeatureFlag | None:
        return self._session.exec(select(FeatureFlag).where(
            FeatureFlag.tenant_id == tenant_id, FeatureFlag.business_id == business_id,
            FeatureFlag.flag_key == flag_key,
        )).first()

    def feature_flags(self, *, tenant_id: str, business_id: str) -> list[FeatureFlag]:
        return list(self._session.exec(select(FeatureFlag).where(
            FeatureFlag.tenant_id == tenant_id,
            FeatureFlag.business_id == business_id,
        ).order_by(FeatureFlag.flag_key)).all())

    def put_feature_flag(self, *, tenant_id: str, business_id: str,
                         flag_key: str, enabled: bool, rollout_percentage: int,
                         config: dict) -> FeatureFlag:
        row = self.feature_flag(tenant_id=tenant_id, business_id=business_id, flag_key=flag_key)
        if row is None:
            row = FeatureFlag(tenant_id=tenant_id, business_id=business_id,
                              flag_key=flag_key, enabled=enabled,
                              rollout_percentage=rollout_percentage, config=config)
            self._session.add(row)
        else:
            row.enabled = enabled
            row.rollout_percentage = rollout_percentage
            row.config = config
            row.updated_at = datetime.now(UTC)
        self._session.flush()
        return row

    def add_metric(self, **values) -> OptimizationMetric:
        row = OptimizationMetric(**values)
        self._session.add(row)
        self._session.flush()
        return row

    def metrics(self, *, tenant_id: str, business_id: str, limit: int = 200):
        statement = select(OptimizationMetric).where(
            OptimizationMetric.tenant_id == tenant_id,
            OptimizationMetric.business_id == business_id,
        ).order_by(OptimizationMetric.period_start.desc()).limit(limit)
        return list(self._session.exec(statement).all())

    def add_audit(self, **values) -> OptimizationAuditLog:
        row = OptimizationAuditLog(**values)
        self._session.add(row)
        self._session.flush()
        return row

    def audit(self, *, tenant_id: str, business_id: str, limit: int = 100):
        statement = select(OptimizationAuditLog).where(
            OptimizationAuditLog.tenant_id == tenant_id,
            OptimizationAuditLog.business_id == business_id,
        ).order_by(OptimizationAuditLog.created_at.desc()).limit(limit)
        return list(self._session.exec(statement).all())
