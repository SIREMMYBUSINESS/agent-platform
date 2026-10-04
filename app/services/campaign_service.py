"""Campaign creation, state transitions, and tenant-scoped reads."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.persistence.unit_of_work import UnitOfWork
from app.schemas import CampaignCreateRequest
from app.services.campaign_sequence_service import CampaignSequenceService


class CampaignTenantMismatchError(Exception):
    """The request tenant assertion differs from trusted configuration."""


class CampaignProvisioningError(Exception):
    """Trusted tenant or business provisioning was not found."""


class CampaignConfigurationError(Exception):
    """Required trusted campaign business scope is not configured."""


class CampaignNotFoundError(Exception):
    """The campaign is not visible in the trusted tenant/business scope."""


class CampaignStateError(Exception):
    """The requested operation is invalid for the campaign state."""


@dataclass(frozen=True)
class CampaignDetails:
    campaign: Any
    sequences: list[tuple[Any, list[Any]]]
    enrollments: list[Any]
    events: list[Any]
    step_runs: list[Any]


CAMPAIGN_TRANSITIONS = {
    "draft": {"active", "canceled"},
    "active": {"paused", "completed", "canceled"},
    "paused": {"active", "canceled"},
    "completed": set(),
    "canceled": set(),
}


class CampaignService:
    def __init__(self, *, trusted_tenant_id: str, trusted_business_id: str | None,
                 uow_factory: Callable[[], UnitOfWork],
                 sequence_service: CampaignSequenceService | None = None) -> None:
        if not trusted_tenant_id.strip():
            raise ValueError("trusted_tenant_id must not be blank")
        self.tenant_id = trusted_tenant_id
        self.business_id = trusted_business_id.strip() if trusted_business_id else None
        self._uow_factory = uow_factory
        self.sequences = sequence_service or CampaignSequenceService()

    def _scope(self) -> tuple[str, str]:
        if not self.business_id:
            raise CampaignConfigurationError("LEAD_BUSINESS_ID must be configured for campaign APIs")
        return self.tenant_id, self.business_id

    def create(self, request: CampaignCreateRequest) -> CampaignDetails:
        tenant_id, business_id = self._scope()
        if request.tenant_id != tenant_id:
            raise CampaignTenantMismatchError("request tenant does not match trusted tenant")
        with self._uow_factory() as uow:
            tenant = uow.tenants.get(tenant_id=tenant_id)
            business = uow.businesses.get(tenant_id=tenant_id, business_id=business_id)
            if tenant is None or business is None:
                raise CampaignProvisioningError("trusted tenant or business is not provisioned")
            campaign = uow.campaigns.create(
                tenant_id=tenant_id, business_id=business_id,
                name=request.name.strip(), channel=request.channel,
                audience_filters=request.audience_filters.model_dump(mode="json"),
            )
            sequence = self.sequences.create_default(
                uow=uow, tenant_id=tenant_id, business_id=business_id,
                campaign_id=campaign.id, steps=request.steps,
            )
            created_event = uow.campaign_events.add(
                tenant_id=tenant_id, business_id=business_id,
                campaign_id=campaign.id, event_type="campaign.created",
                payload={"channel": campaign.channel, "sequence_id": str(sequence.id)},
            )
            return CampaignDetails(
                campaign=self._snapshot(campaign),
                sequences=[(self._snapshot(sequence), [self._snapshot(row) for row in self.sequences.steps_for(
                    uow, tenant_id=tenant_id, business_id=business_id, sequence_id=sequence.id
                )])],
                enrollments=[], events=[self._snapshot(created_event)],
                step_runs=[],
            )

    def get(self, *, campaign_id: UUID) -> CampaignDetails:
        tenant_id, business_id = self._scope()
        with self._uow_factory() as uow:
            campaign = uow.campaigns.get(tenant_id=tenant_id, business_id=business_id, campaign_id=campaign_id)
            if campaign is None:
                raise CampaignNotFoundError("campaign not found")
            sequences = uow.campaign_sequences.list_for_campaign(
                tenant_id=tenant_id, business_id=business_id, campaign_id=campaign.id
            )
            return CampaignDetails(
                campaign=self._snapshot(campaign),
                sequences=[(self._snapshot(sequence), [self._snapshot(step) for step in self.sequences.steps_for(
                    uow, tenant_id=tenant_id, business_id=business_id, sequence_id=sequence.id
                )]) for sequence in sequences],
                enrollments=[self._snapshot(row) for row in uow.campaign_enrollments.list_all(
                    tenant_id=tenant_id, business_id=business_id, campaign_id=campaign_id
                )],
                events=[self._snapshot(row) for row in uow.campaign_events.list_for_campaign(
                    tenant_id=tenant_id, business_id=business_id, campaign_id=campaign_id
                )],
                step_runs=[self._snapshot(row) for row in uow.campaign_step_runs.list_for_campaign(
                    tenant_id=tenant_id, business_id=business_id, campaign_id=campaign_id
                )],
            )

    @staticmethod
    def _snapshot(row: Any) -> Any:
        return type(row)(**row.model_dump())

    @staticmethod
    def transition(*, uow: Any, tenant_id: str, business_id: str,
                   campaign: Any, new_status: str) -> Any:
        previous_status = campaign.status
        if new_status not in CAMPAIGN_TRANSITIONS.get(previous_status, set()):
            raise CampaignStateError(f"cannot transition campaign from {previous_status} to {new_status}")
        updated = uow.campaigns.set_status(
            tenant_id=tenant_id, business_id=business_id,
            campaign_id=campaign.id, expected_status=campaign.status,
            new_status=new_status,
        )
        uow.campaign_events.add(
            tenant_id=tenant_id, business_id=business_id,
            campaign_id=campaign.id, event_type="campaign.state.changed",
            payload={"from": previous_status, "to": new_status},
        )
        if new_status == "completed":
            uow.campaign_events.add(
                tenant_id=tenant_id, business_id=business_id,
                campaign_id=campaign.id, event_type="campaign.completed", payload={},
            )
        return updated


__all__ = [
    "CAMPAIGN_TRANSITIONS", "CampaignConfigurationError", "CampaignDetails", "CampaignNotFoundError",
    "CampaignProvisioningError", "CampaignService", "CampaignStateError",
    "CampaignTenantMismatchError",
]
