"""Deterministic, policy-gated, tenant-scoped outreach recipient selection."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable
from uuid import UUID

from app.persistence.unit_of_work import UnitOfWork
from app.security import AuthenticatedPrincipal
from app.services.policy_service import PolicyService


class RecipientSelectionError(Exception):
    """Campaign is unavailable or caller cannot perform recipient selection."""


@dataclass(frozen=True)
class RecipientSelection:
    campaign_id: UUID
    contact_ids: tuple[str, ...]
    excluded_counts: dict[str, int]


def require_campaign_writer(principal: AuthenticatedPrincipal, now: datetime) -> None:
    if (not isinstance(principal, AuthenticatedPrincipal)
            or principal.issued_at > now
            or "campaign_writer" not in principal.roles
            or (principal.expires_at is not None and principal.expires_at <= now)):
        raise RecipientSelectionError("valid campaign_writer principal required")


class RecipientSelectionService:
    def __init__(self, *, uow_factory: Callable[[], UnitOfWork],
                 policy_service: PolicyService | None = None,
                 now_provider: Callable[[], datetime] | None = None) -> None:
        self._uow_factory = uow_factory
        self._policy = policy_service or PolicyService()
        self._now = now_provider or (lambda: datetime.now(UTC))

    def select(self, principal: AuthenticatedPrincipal, campaign_id: UUID, *,
               contact_ids: list[str] | tuple[str, ...] | None = None,
               limit: int = 500) -> RecipientSelection:
        now = self._now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("recipient selection clock must be timezone-aware")
        now = now.astimezone(UTC)
        require_campaign_writer(principal, now)
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        requested = tuple(sorted(set(contact_ids))) if contact_ids is not None else None
        if requested is not None and len(requested) > limit:
            raise ValueError("contact_ids exceeds selection limit")

        with self._uow_factory() as uow:
            campaign = uow.campaigns.get_for_tenant(
                tenant_id=principal.tenant_id, campaign_id=campaign_id,
            )
            if campaign is None or campaign.status != "active":
                raise RecipientSelectionError("active campaign not found")
            tenant = uow.tenants.get(tenant_id=principal.tenant_id)
            business = uow.businesses.get(
                tenant_id=principal.tenant_id, business_id=campaign.business_id,
            )
            existing = uow.outreach_messages.list_contact_ids(
                tenant_id=principal.tenant_id, business_id=campaign.business_id,
                campaign_id=campaign.id,
            )
            if requested is None:
                contacts = uow.contacts.list_for_business(
                    tenant_id=principal.tenant_id, business_id=campaign.business_id,
                    limit=limit,
                )
            else:
                contacts = [uow.contacts.get(tenant_id=principal.tenant_id, contact_id=item)
                            for item in requested]
            exclusions: Counter[str] = Counter()
            eligible = []
            for contact in contacts:
                if contact is None or contact.business_id != campaign.business_id:
                    exclusions["not_found_in_campaign_scope"] += 1
                    continue
                if contact.id in existing:
                    exclusions["already_selected"] += 1
                    continue
                decision = self._policy.evaluate(
                    uow, tenant=tenant, business=business, contact=contact,
                    channel=campaign.channel, direction="outbound",
                )
                if not decision.allowed:
                    exclusions[decision.reason_code] += 1
                    continue
                eligible.append(contact.id)
            return RecipientSelection(campaign.id, tuple(eligible), dict(sorted(exclusions.items())))

__all__ = ["RecipientSelection", "RecipientSelectionError", "RecipientSelectionService"]
