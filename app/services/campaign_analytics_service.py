"""Tenant-scoped campaign metrics backed by persisted runs, replies, and events."""
from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from app.services.reply_service import CampaignReplyNotFoundError


class CampaignAnalyticsService:
    def __init__(self, *, trusted_tenant_id: str, trusted_business_id: str | None,
                 uow_factory: Callable) -> None:
        self._tenant_id = trusted_tenant_id
        self._business_id = trusted_business_id.strip() if trusted_business_id else None
        self._uow_factory = uow_factory

    def get(self, campaign_id: UUID) -> dict:
        if not self._business_id:
            raise CampaignReplyNotFoundError("campaign is not available in trusted business scope")
        with self._uow_factory() as uow:
            result = uow.campaign_analytics.aggregate(
                tenant_id=self._tenant_id, business_id=self._business_id,
                campaign_id=campaign_id,
            )
            if result is None:
                raise CampaignReplyNotFoundError("campaign not found")
            return result


__all__ = ["CampaignAnalyticsService"]
