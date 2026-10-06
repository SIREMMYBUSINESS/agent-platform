"""Campaign creation, state transitions, and tenant-scoped reads."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.persistence.unit_of_work import UnitOfWork
from app.schemas import CampaignCreateRequest
from app.services.campaign_sequence_service import CampaignSequenceService
from datetime import UTC, datetime
from app.security import AuthenticatedPrincipal


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
    "draft": {"active", "canceled", "archived"},
    "active": {"paused", "completed", "canceled", "archived"},
    "paused": {"active", "canceled", "archived"},
    "completed": set(),
    "canceled": {"archived"},
    "archived": set(),
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

    def _principal_scope(self, principal: AuthenticatedPrincipal, *, role: str) -> tuple[str, str]:
        now = datetime.now(UTC)
        if (not isinstance(principal, AuthenticatedPrincipal)
                or principal.issued_at > now
                or principal.expires_at is not None and principal.expires_at <= now
                or role not in principal.roles):
            raise CampaignTenantMismatchError(f"campaign {role.removeprefix('campaign_')} principal required")
        if not self.business_id:
            raise CampaignConfigurationError("LEAD_BUSINESS_ID must be configured for campaign APIs")
        return principal.tenant_id, self.business_id

    def create(self, request: CampaignCreateRequest,
               principal: AuthenticatedPrincipal) -> CampaignDetails:
        tenant_id, business_id = self._principal_scope(principal, role="campaign_writer")
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
            sequences = [sequence]
            for order, variant in enumerate(request.sequence_variants, start=1):
                sequences.append(self.sequences.create_variant(
                    uow=uow, tenant_id=tenant_id, business_id=business_id,
                    campaign_id=campaign.id, name=variant.name,
                    sequence_order=order, steps=variant.steps,
                ))
            created_event = uow.campaign_events.add(
                tenant_id=tenant_id, business_id=business_id,
                campaign_id=campaign.id, event_type="campaign.created",
                payload={"channel": campaign.channel, "sequence_id": str(sequence.id),
                         "sequence_ids": [str(item.id) for item in sequences],
                         "experiment_enabled": len(sequences) > 1,
                         "actor_id": principal.principal_id, "domain_event": "CampaignCreated"},
            )
            created_domain_event = uow.campaign_events.add(
                tenant_id=tenant_id, business_id=business_id,
                campaign_id=campaign.id, event_type="CampaignCreated",
                payload={"actor_id": principal.principal_id,
                         "channel": campaign.channel},
            )
            return CampaignDetails(
                campaign=self._snapshot(campaign),
                sequences=[(self._snapshot(item), [self._snapshot(row) for row in self.sequences.steps_for(
                    uow, tenant_id=tenant_id, business_id=business_id, sequence_id=item.id
                )]) for item in sequences],
                enrollments=[], events=[self._snapshot(created_event)] + (
                    [self._snapshot(created_domain_event)] if created_domain_event else []
                ),
                step_runs=[],
            )

    def get(self, principal: AuthenticatedPrincipal, *, campaign_id: UUID) -> CampaignDetails:
        tenant_id, business_id = self._principal_scope(principal, role="campaign_reader")
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

    def activate(self, principal: AuthenticatedPrincipal, campaign_id: UUID) -> Any:
        return self._change_status(principal, campaign_id, "active")

    def pause(self, principal: AuthenticatedPrincipal, campaign_id: UUID) -> Any:
        return self._change_status(principal, campaign_id, "paused")

    def archive(self, principal: AuthenticatedPrincipal, campaign_id: UUID) -> Any:
        return self._change_status(principal, campaign_id, "archived")

    def _change_status(self, principal: AuthenticatedPrincipal, campaign_id: UUID,
                       new_status: str) -> Any:
        tenant_id, business_id = self._principal_scope(principal, role="campaign_writer")
        with self._uow_factory() as uow:
            campaign = uow.campaigns.get(tenant_id=tenant_id, business_id=business_id,
                                         campaign_id=campaign_id)
            if campaign is None:
                raise CampaignNotFoundError("campaign not found")
            if campaign.status == new_status:
                return self._snapshot(campaign)
            previous = campaign.status
            updated = self.transition(uow=uow, tenant_id=tenant_id,
                                      business_id=business_id, campaign=campaign,
                                      new_status=new_status)
            event_name = {"active": "CampaignActivated"}.get(new_status)
            if event_name:
                uow.campaign_events.add(
                    tenant_id=tenant_id, business_id=business_id,
                    campaign_id=campaign.id, event_type=event_name,
                    payload={"actor_id": principal.principal_id,
                             "from": previous, "to": new_status},
                )
            return self._snapshot(updated)


__all__ = [
    "CAMPAIGN_TRANSITIONS", "CampaignConfigurationError", "CampaignDetails", "CampaignNotFoundError",
    "CampaignProvisioningError", "CampaignService", "CampaignStateError",
    "CampaignTenantMismatchError",
]
