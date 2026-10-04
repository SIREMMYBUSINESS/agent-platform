"""Trusted, transactional import and read operations for sales leads."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.persistence.models import Business, Contact, Lead, LeadEnrichment, LeadEventLog, LeadScore, LeadStateTransition, Tenant
from app.persistence.unit_of_work import UnitOfWork
from app.schemas import LeadImportRequest
from app.services.enrichment_service import EnrichmentAdapter, EnrichmentService
from app.services.lead_scoring_service import LeadScoringService


class LeadTenantMismatchError(Exception):
    """The supplied tenant assertion differs from trusted configuration."""


class LeadProvisioningError(Exception):
    """A trusted tenant/business/contact is missing or incorrectly related."""


class LeadConfigurationError(Exception):
    """Required trusted lead-import scope has not been configured."""


@dataclass(frozen=True)
class LeadDetails:
    lead: Lead
    enrichment: LeadEnrichment | None
    score: LeadScore | None
    transitions: list[LeadStateTransition]
    events: list[LeadEventLog]


class LeadImportService:
    def __init__(
        self,
        *,
        trusted_tenant_id: str,
        trusted_business_id: str | None,
        uow_factory: Callable[[], UnitOfWork],
        enrichment_adapter: EnrichmentAdapter | None = None,
        scoring_service: LeadScoringService | None = None,
    ) -> None:
        if not trusted_tenant_id.strip():
            raise ValueError("trusted_tenant_id must not be blank")
        self._tenant_id = trusted_tenant_id
        self._business_id = trusted_business_id.strip() if trusted_business_id else None
        self._uow_factory = uow_factory
        self._enrichment = EnrichmentService(enrichment_adapter)
        self._scoring = scoring_service or LeadScoringService()

    def _scope(self) -> tuple[str, str]:
        if not self._business_id:
            raise LeadConfigurationError("LEAD_BUSINESS_ID must be configured for lead APIs")
        return self._tenant_id, self._business_id

    @staticmethod
    def _snapshot(row: Any) -> Any:
        """Copy persisted fields before the Unit of Work expires its session."""
        if row is None:
            return None
        return type(row)(**row.model_dump())

    @staticmethod
    def _event(uow: Any, *, tenant_id: str, business_id: str, lead_id: UUID,
               event_type: str, payload: dict[str, Any]) -> None:
        uow.lead_events.add(
            tenant_id=tenant_id, business_id=business_id, lead_id=lead_id,
            event_type=event_type, payload=payload,
        )

    def import_lead(self, request: LeadImportRequest) -> LeadDetails:
        tenant_id, business_id = self._scope()
        if request.tenant_id != tenant_id:
            raise LeadTenantMismatchError("request tenant does not match trusted tenant")

        with self._uow_factory() as uow:
            tenant: Tenant | None = uow.tenants.get(tenant_id=tenant_id)
            business: Business | None = uow.businesses.get(
                tenant_id=tenant_id, business_id=business_id
            )
            contact: Contact | None = uow.contacts.get(
                tenant_id=tenant_id, contact_id=request.contact_id
            )
            if tenant is None or business is None or contact is None:
                raise LeadProvisioningError("tenant, business, or contact is not provisioned")
            if contact.business_id != business.id:
                raise LeadProvisioningError("contact is not provisioned for the trusted business")

            source = uow.lead_sources.get_or_create(
                tenant_id=tenant_id,
                business_id=business_id,
                name=request.source_name,
                source_type=request.source_type,
                external_reference=request.source_reference,
                source_metadata=request.source_metadata,
            )
            lead = uow.leads.create(
                tenant_id=tenant_id,
                business_id=business_id,
                contact_id=contact.id,
                source_id=source.id,
            )
            uow.lead_transitions.record_imported(
                tenant_id=tenant_id, business_id=business_id, lead_id=lead.id
            )
            self._event(uow, tenant_id=tenant_id, business_id=business_id,
                        lead_id=lead.id, event_type="lead.imported",
                        payload={"source_id": str(source.id)})

            enrichment_result = self._enrichment.collect(contact=contact, business=business)
            enrichment_values = enrichment_result.values
            if enrichment_result.failed:
                self._event(uow, tenant_id=tenant_id, business_id=business_id,
                            lead_id=lead.id, event_type="lead.enrichment.failed",
                            payload={"provider": enrichment_result.provider_name})
            if enrichment_values:
                uow.lead_enrichments.add(
                    tenant_id=tenant_id, business_id=business_id, lead_id=lead.id,
                    values=enrichment_values, provider_name=enrichment_result.provider_name,
                )
                uow.lead_transitions.transition(
                    tenant_id=tenant_id, business_id=business_id, lead_id=lead.id,
                    expected_state="imported", new_state="enriched", context={},
                )
                self._event(uow, tenant_id=tenant_id, business_id=business_id,
                            lead_id=lead.id, event_type="lead.enriched",
                            payload={"provider": enrichment_result.provider_name})

            score = self._scoring.score(enrichment_values)
            persisted_score = uow.lead_scores.add(
                tenant_id=tenant_id, business_id=business_id, lead_id=lead.id,
                scores=score.persistence_values(),
            )
            previous = "enriched" if enrichment_values else "imported"
            uow.lead_transitions.transition(
                tenant_id=tenant_id, business_id=business_id, lead_id=lead.id,
                expected_state=previous, new_state="scored",
                context={"priority_score": score.priority_score},
            )
            self._event(uow, tenant_id=tenant_id, business_id=business_id,
                        lead_id=lead.id, event_type="lead.scored",
                        payload={"priority_score": score.priority_score})

            status = "scored"
            if score.priority_score >= self._scoring.qualification_threshold:
                uow.lead_transitions.transition(
                    tenant_id=tenant_id, business_id=business_id, lead_id=lead.id,
                    expected_state="scored", new_state="qualified",
                    context={"qualification_threshold": self._scoring.qualification_threshold},
                )
                self._event(uow, tenant_id=tenant_id, business_id=business_id,
                            lead_id=lead.id, event_type="lead.qualified",
                            payload={"priority_score": score.priority_score})
                status = "qualified"

            # Keep the returned model synchronized with the persisted state.
            lead.status = status
            return LeadDetails(
                lead=self._snapshot(lead),
                enrichment=self._snapshot(enrichment_values and uow.lead_enrichments.latest(
                    tenant_id=tenant_id, business_id=business_id, lead_id=lead.id
                )),
                score=self._snapshot(persisted_score),
                transitions=[self._snapshot(row) for row in uow.lead_transitions.history(
                    tenant_id=tenant_id, business_id=business_id, lead_id=lead.id
                )],
                events=[self._snapshot(row) for row in uow.lead_events.list_for_lead(
                    tenant_id=tenant_id, business_id=business_id, lead_id=lead.id
                )],
            )

    def get(self, *, lead_id: UUID) -> LeadDetails:
        tenant_id, business_id = self._scope()
        with self._uow_factory() as uow:
            lead = uow.leads.get(tenant_id=tenant_id, business_id=business_id, lead_id=lead_id)
            if lead is None:
                raise LeadProvisioningError("lead not found")
            return LeadDetails(
                lead=self._snapshot(lead),
                enrichment=self._snapshot(uow.lead_enrichments.latest(tenant_id=tenant_id, business_id=business_id, lead_id=lead.id)),
                score=self._snapshot(uow.lead_scores.latest(tenant_id=tenant_id, business_id=business_id, lead_id=lead.id)),
                transitions=[self._snapshot(row) for row in uow.lead_transitions.history(tenant_id=tenant_id, business_id=business_id, lead_id=lead.id)],
                events=[self._snapshot(row) for row in uow.lead_events.list_for_lead(tenant_id=tenant_id, business_id=business_id, lead_id=lead.id)],
            )

    def list(self, *, offset: int = 0, limit: int = 50) -> list[LeadDetails]:
        tenant_id, business_id = self._scope()
        with self._uow_factory() as uow:
            leads = uow.leads.list(tenant_id=tenant_id, business_id=business_id, offset=offset, limit=limit)
            return [LeadDetails(
                lead=self._snapshot(lead),
                enrichment=self._snapshot(uow.lead_enrichments.latest(tenant_id=tenant_id, business_id=business_id, lead_id=lead.id)),
                score=self._snapshot(uow.lead_scores.latest(tenant_id=tenant_id, business_id=business_id, lead_id=lead.id)),
                transitions=[], events=[],
            ) for lead in leads]


__all__ = [
    "LeadConfigurationError", "LeadDetails", "LeadImportService",
    "LeadProvisioningError", "LeadTenantMismatchError",
]
