"""Dental recall imports and policy-gated enrollment in existing campaigns."""
from __future__ import annotations

import calendar
from datetime import UTC, datetime

from app.schemas import CampaignEnrollRequest
from app.security import AuthenticatedPrincipal
from app.services.enrollment_service import EnrollmentService
from app.services.policy_service import PolicyService


class DentalOutreachService:
    def __init__(self, *, uow_factory, policy_service: PolicyService | None = None,
                 now_provider=None):
        self._uow_factory = uow_factory
        self._policy = policy_service or PolicyService()
        self._now = now_provider or (lambda: datetime.now(UTC))

    def enroll_overdue(self, principal: AuthenticatedPrincipal, *, business_id: str,
                       campaign_id, months_since_visit: int = 6, limit: int = 500):
        if (not isinstance(principal, AuthenticatedPrincipal)
                or "campaign_writer" not in principal.roles):
            raise PermissionError("vertical campaign operation denied")
        if not 1 <= months_since_visit <= 60 or not 1 <= limit <= 500:
            raise ValueError("recall period or import limit is out of range")
        cutoff = _subtract_months(self._now(), months_since_visit)
        with self._uow_factory() as uow:
            business = uow.businesses.get(tenant_id=principal.tenant_id,
                                          business_id=business_id)
            profile = uow.verticals.profile(tenant_id=principal.tenant_id,
                                             business_id=business_id)
            campaign = uow.campaigns.get(tenant_id=principal.tenant_id,
                business_id=business_id, campaign_id=campaign_id)
            if (business is None or profile is None or campaign is None
                    or profile.vertical_id != "healthcare_dental"):
                raise LookupError("dental campaign scope was not found")
            if campaign.channel not in {"sms", "email"}:
                raise ValueError("dental reactivation supports SMS or email")
            tenant = uow.tenants.get(tenant_id=principal.tenant_id)
            source = uow.lead_sources.get_or_create(
                tenant_id=principal.tenant_id, business_id=business_id,
                name="overdue_patient_reactivation", source_type="patient_recall",
            )
            leads = []
            excluded = 0
            contacts = uow.contacts.list_overdue_visits(
                tenant_id=principal.tenant_id, business_id=business_id,
                cutoff=cutoff, limit=limit,
            )
            for contact in contacts:
                prior = uow.leads.latest_for_contact(
                    tenant_id=principal.tenant_id, business_id=business_id,
                    contact_id=contact.id,
                )
                if prior is not None:
                    if prior.status == "qualified":
                        leads.append(prior.id)
                    continue
                decision = self._policy.evaluate(
                    uow, tenant=tenant, business=business, contact=contact,
                    channel=campaign.channel, direction="outbound",
                )
                if not decision.allowed:
                    excluded += 1
                    continue
                lead = uow.leads.create(
                    tenant_id=principal.tenant_id, business_id=business_id,
                    contact_id=contact.id, source_id=source.id,
                )
                uow.lead_transitions.transition(
                    tenant_id=principal.tenant_id, business_id=business_id,
                    lead_id=lead.id, expected_state="imported", new_state="enriched",
                    context={"vertical_rule": "overdue_patient_reactivation"},
                )
                score = {"fit_score": 70, "urgency_score": 30, "intent_score": 65,
                         "priority_score": 65,
                         "rationale": {"rule": "overdue_patient_reactivation",
                                       "threshold_months": months_since_visit}}
                uow.lead_scores.add(tenant_id=principal.tenant_id,
                    business_id=business_id, lead_id=lead.id, scores=score)
                uow.lead_transitions.transition(
                    tenant_id=principal.tenant_id, business_id=business_id,
                    lead_id=lead.id, expected_state="enriched", new_state="scored",
                    context={"priority_score": score["priority_score"]},
                )
                uow.lead_transitions.transition(
                    tenant_id=principal.tenant_id, business_id=business_id,
                    lead_id=lead.id, expected_state="scored", new_state="qualified",
                    context={"qualification_rule": "overdue_patient_reactivation"},
                )
                uow.lead_events.add(tenant_id=principal.tenant_id,
                    business_id=business_id, lead_id=lead.id,
                    event_type="lead.qualified",
                    payload={"qualification_rule": "overdue_patient_reactivation",
                             "threshold_months": months_since_visit})
                leads.append(lead.id)
        if not leads:
            return {"campaign_id": campaign_id, "lead_ids": [], "enrolled": 0,
                    "excluded_by_policy": excluded}
        enrollment = EnrollmentService(
            trusted_tenant_id=principal.tenant_id,
            trusted_business_id=business_id, uow_factory=self._uow_factory,
        ).enroll(campaign_id, CampaignEnrollRequest(
            tenant_id=principal.tenant_id, lead_ids=leads,
        ))
        return {"campaign_id": campaign_id, "lead_ids": leads,
                "enrolled": len(enrollment), "excluded_by_policy": excluded}


def _subtract_months(value: datetime, months: int) -> datetime:
    year = value.year
    month = value.month - months
    while month <= 0:
        month += 12
        year -= 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


__all__ = ["DentalOutreachService"]
