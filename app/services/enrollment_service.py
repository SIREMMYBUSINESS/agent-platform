"""Campaign lead enrollment and idempotent sequence-step orchestration."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import hashlib
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from app.persistence.unit_of_work import UnitOfWork
from app.services.policy_service import PolicyService
from app.schemas import CampaignEnrollRequest, CampaignTriggerRequest
from app.services.campaign_service import (
    CampaignConfigurationError,
    CampaignNotFoundError,
    CampaignProvisioningError,
    CampaignService,
    CampaignStateError,
    CampaignTenantMismatchError,
)
from app.services.campaign_sequence_service import CampaignSequenceService
from app.services.sequence_experiments import SequenceExperimentService
from app.queue.contracts import QueueDomain
from app.services.contact_preference_service import ContactPreferenceService
from app.services.outbound_delivery_service import OutboundDeliveryService
from app.optimization.feedback import FeedbackEventCapture
from app.optimization.segments import segment_for_lead


class CampaignEligibilityError(Exception):
    """A lead does not satisfy campaign audience or contact policy."""


class EnrollmentService:
    def __init__(self, *, trusted_tenant_id: str, trusted_business_id: str | None,
                 uow_factory: Callable[[], UnitOfWork],
                 sequence_service: CampaignSequenceService | None = None,
                 policy_service: PolicyService | None = None,
                 now_provider: Callable[[], datetime] | None = None) -> None:
        self.tenant_id = trusted_tenant_id
        self.business_id = trusted_business_id.strip() if trusted_business_id else None
        self._uow_factory = uow_factory
        self._sequences = sequence_service or CampaignSequenceService()
        self._experiments = SequenceExperimentService()
        self._policy_service = policy_service or PolicyService()
        self._now = now_provider or (lambda: datetime.now(UTC))
        self._feedback = FeedbackEventCapture()

    def _scope(self) -> tuple[str, str]:
        if not self.business_id:
            raise CampaignConfigurationError("LEAD_BUSINESS_ID must be configured for campaign APIs")
        return self.tenant_id, self.business_id

    def enroll(self, campaign_id: UUID, request: CampaignEnrollRequest) -> list[Any]:
        tenant_id, business_id = self._scope()
        if request.tenant_id != tenant_id:
            raise CampaignTenantMismatchError("request tenant does not match trusted tenant")
        now = self._now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        now = now.astimezone(UTC)
        with self._uow_factory() as uow:
            campaign = uow.campaigns.get(tenant_id=tenant_id, business_id=business_id, campaign_id=campaign_id)
            if campaign is None:
                raise CampaignNotFoundError("campaign not found")
            if campaign.status not in {"draft", "active"}:
                raise CampaignStateError(f"cannot enroll leads while campaign is {campaign.status}")
            sequences = sorted(uow.campaign_sequences.list_for_campaign(
                tenant_id=tenant_id, business_id=business_id, campaign_id=campaign.id
            ), key=lambda item: (item.sequence_order, str(item.id)))
            if not sequences:
                raise CampaignProvisioningError("campaign sequence is missing")

            leads = []
            for lead_id in dict.fromkeys(request.lead_ids):
                lead = uow.leads.get(tenant_id=tenant_id, business_id=business_id, lead_id=lead_id)
                if lead is None:
                    raise CampaignProvisioningError("lead not found in trusted campaign scope")
                self._assert_eligible(uow, campaign, lead, tenant_id, business_id)
                leads.append(lead)

            result = []
            for lead in leads:
                sequence_by_name = {item.name: item for item in sequences}
                experiment_group = ("test_group_a" if int.from_bytes(
                    hashlib.sha256(f"{campaign.id}:{lead.id}".encode()).digest()[:2], "big") % 2 == 0
                    else "test_group_b")
                weights = {name: 1 for name in sequence_by_name}
                variant_flag = uow.optimization.feature_flag(
                    tenant_id=tenant_id, business_id=business_id,
                    flag_key="optimization.message_variants",
                )
                variant_applied = False
                if variant_flag and variant_flag.enabled and int.from_bytes(
                    hashlib.sha256(f"variant:{lead.id}".encode()).digest()[:4], "big") % 100 < variant_flag.rollout_percentage:
                    change = variant_flag.config
                    if (change.get("campaign_id") == str(campaign.id)
                            and change.get("test_group") in {"all", experiment_group}
                            and isinstance(change.get("weights"), dict)):
                        proposed_weights = change["weights"]
                        weights = {name: int(proposed_weights.get(name, 0))
                                   for name in sequence_by_name
                                   if int(proposed_weights.get(name, 0)) > 0}
                        if not weights:
                            weights = {name: 1 for name in sequence_by_name}
                        else:
                            variant_applied = True
                variant = self._experiments.assign_variant(
                    tenant_id=tenant_id, business_id=business_id,
                    campaign_id=str(campaign.id), lead_id=str(lead.id),
                    experiment_id=f"campaign-sequence:{campaign.id}",
                    weights=weights,
                )
                sequence = sequence_by_name[variant]
                steps = self._sequences.steps_for(
                    uow, tenant_id=tenant_id, business_id=business_id,
                    sequence_id=sequence.id,
                )
                if not steps:
                    raise CampaignProvisioningError("campaign sequence has no steps")
                next_step_at = now + timedelta(seconds=steps[0].delay_seconds)
                timing_flag = uow.optimization.feature_flag(
                    tenant_id=tenant_id, business_id=business_id,
                    flag_key="optimization.send_time",
                )
                timing_applied = False
                if (timing_flag and timing_flag.enabled and int.from_bytes(
                        hashlib.sha256(f"timing:{lead.id}".encode()).digest()[:4], "big") % 100
                        < timing_flag.rollout_percentage):
                    change = timing_flag.config
                    lead_segment = segment_for_lead(
                        uow, tenant_id=tenant_id, business_id=business_id, lead_id=lead.id,
                    )
                    if change.get("segment") in {lead_segment, "all"} and isinstance(change.get("send_hour"), int):
                        candidate = next_step_at.replace(hour=change["send_hour"], minute=0, second=0, microsecond=0)
                        if candidate < next_step_at:
                            candidate += timedelta(days=1)
                        next_step_at = candidate
                        timing_applied = True
                existing = uow.campaign_enrollments.get_for_lead(
                    tenant_id=tenant_id, business_id=business_id,
                    campaign_id=campaign.id, lead_id=lead.id,
                )
                enrollment = uow.campaign_enrollments.get_or_create(
                    tenant_id=tenant_id, business_id=business_id,
                    campaign_id=campaign.id, sequence_id=sequence.id,
                    lead_id=lead.id, next_step_at=next_step_at,
                    experiment_group=experiment_group,
                )
                correlation_id = f"campaign:{campaign.id}:enrollment:{enrollment.id}"
                self._feedback.capture(
                    uow=uow, tenant_id=tenant_id, business_id=business_id,
                    event_key=f"flag-exposure:optimization.message_variants:{enrollment.id}",
                    correlation_id=correlation_id, event_type="feature_flag_exposure",
                    outcome="exposed", lead_id=lead.id, campaign_id=campaign.id,
                    decision={"flag_key": "optimization.message_variants",
                              "applied": variant_applied},
                    event_metadata={"test_group": experiment_group},
                )
                self._feedback.capture(
                    uow=uow, tenant_id=tenant_id, business_id=business_id,
                    event_key=f"flag-exposure:optimization.send_time:{enrollment.id}",
                    correlation_id=correlation_id, event_type="feature_flag_exposure",
                    outcome="exposed", lead_id=lead.id, campaign_id=campaign.id,
                    decision={"flag_key": "optimization.send_time",
                              "applied": timing_applied},
                    event_metadata={"test_group": experiment_group},
                )
                if existing is None:
                    uow.campaign_events.add(
                        tenant_id=tenant_id, business_id=business_id,
                        campaign_id=campaign.id, enrollment_id=enrollment.id,
                        event_type="campaign.enrolled",
                        payload={"lead_id": str(lead.id),
                                 "sequence_id": str(sequence.id),
                                 "variant_id": sequence.name,
                                 "experiment_id": f"campaign-sequence:{campaign.id}"},
                    )
                result.append(self._snapshot(enrollment))
            return result

    def trigger(self, campaign_id: UUID, request: CampaignTriggerRequest) -> dict[str, Any]:
        tenant_id, business_id = self._scope()
        if request.tenant_id != tenant_id:
            raise CampaignTenantMismatchError("request tenant does not match trusted tenant")
        now = self._now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        now = now.astimezone(UTC)
        with self._uow_factory() as uow:
            campaign = uow.campaigns.get(tenant_id=tenant_id, business_id=business_id, campaign_id=campaign_id)
            if campaign is None:
                raise CampaignNotFoundError("campaign not found")
            if campaign.status == "draft":
                campaign = CampaignService.transition(
                    uow=uow, tenant_id=tenant_id, business_id=business_id,
                    campaign=campaign, new_status="active",
                )
            elif campaign.status == "completed":
                return {"campaign_id": campaign.id, "status": campaign.status,
                        "processed": 0, "sent": 0, "skipped": 0}
            elif campaign.status != "active":
                raise CampaignStateError(f"cannot trigger campaign while {campaign.status}")

            enrollments = (
                [uow.campaign_enrollments.get(
                    tenant_id=tenant_id, business_id=business_id,
                    campaign_id=campaign.id, enrollment_id=request.enrollment_id,
                )]
                if request.enrollment_id
                else uow.campaign_enrollments.list_active(
                    tenant_id=tenant_id, business_id=business_id, campaign_id=campaign.id
                )
            )
            if request.enrollment_id and enrollments[0] is None:
                raise CampaignNotFoundError("campaign enrollment not found")

            processed = sent = skipped = queued = 0
            for enrollment in enrollments:
                due_at = enrollment.next_step_at
                if due_at is not None and due_at.tzinfo is None:
                    due_at = due_at.replace(tzinfo=UTC)
                if enrollment.status != "active" or due_at is None or due_at > now:
                    continue
                step = self._sequences.next_step(
                    uow=uow, tenant_id=tenant_id, business_id=business_id,
                    sequence_id=enrollment.sequence_id,
                    current_step_order=enrollment.current_step_order,
                )
                if step is None:
                    uow.campaign_enrollments.advance(
                        tenant_id=tenant_id, business_id=business_id,
                        enrollment_id=enrollment.id, expected_status="active",
                        current_step_order=enrollment.current_step_order,
                        status="completed", next_step_at=None,
                    )
                    continue
                if uow.campaign_step_runs.exists(
                    tenant_id=tenant_id, business_id=business_id,
                    enrollment_id=enrollment.id, step_id=step.id,
                ):
                    continue

                eligible, reason, contact, enrichment = self._is_eligible(
                    uow, campaign, enrollment.lead_id, tenant_id, business_id
                )
                processed += 1
                if not eligible:
                    uow.campaign_step_runs.add(
                        tenant_id=tenant_id, business_id=business_id,
                        enrollment_id=enrollment.id, step_id=step.id,
                        step_order=step.step_order, status="skipped",
                        rendered_content={}, skip_reason=reason,
                        generated_at=now, sent_at=None,
                    )
                    uow.campaign_events.add(
                        tenant_id=tenant_id, business_id=business_id,
                        campaign_id=campaign.id, enrollment_id=enrollment.id,
                        event_type="step.skipped",
                        payload={"step_order": step.step_order, "reason": reason},
                    )
                    uow.campaign_enrollments.advance(
                        tenant_id=tenant_id, business_id=business_id,
                        enrollment_id=enrollment.id, expected_status="active",
                        current_step_order=step.step_order, status="skipped", next_step_at=None,
                    )
                    skipped += 1
                    continue

                rendered = self._render(step.template_data, contact=contact,
                                        business=uow.businesses.get(tenant_id=tenant_id, business_id=business_id),
                                        enrichment=enrichment)
                step_correlation_id = (
                    f"campaign:{campaign.id}:enrollment:{enrollment.id}:step:{step.step_order}"
                )
                conversation = uow.conversations.get_by_correlation_id(
                    tenant_id=tenant_id, business_id=business_id,
                    correlation_id=step_correlation_id,
                )
                if conversation is None:
                    conversation = uow.conversations.create(
                        tenant_id=tenant_id, business_id=business_id,
                        contact_id=contact.id, channel=campaign.channel,
                        correlation_id=step_correlation_id,
                    )
                run = uow.campaign_step_runs.add(
                    tenant_id=tenant_id, business_id=business_id,
                    enrollment_id=enrollment.id, step_id=step.id,
                    step_order=step.step_order, status="queued",
                    rendered_content=rendered, skip_reason=None,
                    generated_at=now, sent_at=None,
                )
                channel = (ContactPreferenceService().choose_channel(
                    contact=contact, fallback=campaign.channel,
                ) or campaign.channel)
                message_id = uuid5(NAMESPACE_URL, f"campaign-step-run:{run.id}")
                body = rendered.get("body") or rendered.get("text") or ""
                message = OutboundDeliveryService().queue(
                    uow, tenant=uow.tenants.get(tenant_id=tenant_id),
                    business=uow.businesses.get(tenant_id=tenant_id, business_id=business_id),
                    contact=contact, conversation=conversation, channel=channel,
                    content=body, subject=rendered.get("subject"),
                    message_id=message_id, correlation_id=step_correlation_id,
                    campaign_step_run_id=run.id,
                )
                uow.campaign_step_runs.set_status(
                    tenant_id=tenant_id, business_id=business_id, run_id=run.id,
                    status="blocked" if message.delivery_status == "blocked" else
                           "sent" if message.delivery_status == "sent" else "queued",
                    sent_at=now if message.delivery_status == "sent" else None,
                    skip_reason="delivery_blocked" if message.delivery_status == "blocked" else None,
                    message_id=message.id,
                )
                uow.campaign_events.add(
                    tenant_id=tenant_id, business_id=business_id,
                    campaign_id=campaign.id, enrollment_id=enrollment.id,
                    event_type="step.generated",
                    payload={"step_order": step.step_order, "run_id": str(run.id)},
                )
                uow.campaign_events.add(
                    tenant_id=tenant_id, business_id=business_id,
                    campaign_id=campaign.id, enrollment_id=enrollment.id,
                    event_type="step.blocked" if message.delivery_status == "blocked" else
                               "step.sent" if message.delivery_status == "sent" else "step.queued",
                    payload={"step_order": step.step_order, "run_id": str(run.id),
                             "message_id": str(message.id),
                             "delivery_status": message.delivery_status},
                )
                next_step = self._sequences.next_step(
                    uow=uow, tenant_id=tenant_id, business_id=business_id,
                    sequence_id=enrollment.sequence_id,
                    current_step_order=step.step_order,
                )
                status = "active" if next_step else "completed"
                next_due = now + timedelta(seconds=next_step.delay_seconds) if next_step else None
                uow.campaign_enrollments.advance(
                    tenant_id=tenant_id, business_id=business_id,
                    enrollment_id=enrollment.id, expected_status="active",
                    current_step_order=step.step_order, status=status,
                    next_step_at=next_due,
                )
                if message.delivery_status == "blocked":
                    uow.campaign_reply_enrollments.cancel(
                        tenant_id=tenant_id, business_id=business_id,
                        campaign_id=campaign.id, enrollment_id=enrollment.id,
                    )
                    skipped += 1
                elif message.delivery_status == "sent":
                    sent += 1
                else:
                    queued += 1
                if next_step is not None and status == "active":
                    uow.queue_tasks.enqueue(
                        tenant_id=tenant_id, business_id=business_id,
                        domain=QueueDomain.OUTREACH.value,
                        task_name="outreach.trigger_enrollment",
                        idempotency_key=(f"campaign:{campaign.id}:enrollment:{enrollment.id}:"
                                         f"step:{next_step.step_order}"),
                        aggregate_type="campaign_enrollment", aggregate_id=str(enrollment.id),
                        payload={"campaign_id": str(campaign.id),
                                 "enrollment_id": str(enrollment.id)},
                        correlation_id=step_correlation_id, traceparent=None,
                        available_at=next_due, max_attempts=6,
                    )

            all_enrollments = uow.campaign_enrollments.list_all(
                tenant_id=tenant_id, business_id=business_id, campaign_id=campaign.id
            )
            remaining = uow.campaign_enrollments.list_active(
                tenant_id=tenant_id, business_id=business_id, campaign_id=campaign.id
            )
            if all_enrollments and not remaining:
                refreshed = uow.campaigns.get(
                    tenant_id=tenant_id, business_id=business_id, campaign_id=campaign.id
                )
                if refreshed and refreshed.status == "active":
                    campaign = CampaignService.transition(
                        uow=uow, tenant_id=tenant_id, business_id=business_id,
                        campaign=refreshed, new_status="completed",
                    )
            else:
                campaign = uow.campaigns.get(
                    tenant_id=tenant_id, business_id=business_id, campaign_id=campaign.id
                )
            return {"campaign_id": campaign.id, "status": campaign.status,
                    "processed": processed, "sent": sent, "queued": queued,
                    "skipped": skipped}

    def _assert_eligible(self, uow: Any, campaign: Any, lead: Any,
                         tenant_id: str, business_id: str) -> None:
        eligible, reason, _, _ = self._is_eligible(
            uow, campaign, lead.id, tenant_id, business_id, lead=lead
        )
        if not eligible:
            raise CampaignEligibilityError(reason)

    def _is_eligible(self, uow: Any, campaign: Any, lead_id: UUID,
                     tenant_id: str, business_id: str, lead: Any = None):
        lead = lead or uow.leads.get(tenant_id=tenant_id, business_id=business_id, lead_id=lead_id)
        if lead is None or lead.business_id != business_id:
            return False, "lead not found in campaign business", None, None
        if lead.status != "qualified":
            return False, "lead is not qualified", None, None
        score = uow.lead_scores.latest(tenant_id=tenant_id, business_id=business_id, lead_id=lead.id)
        filters = campaign.audience_filters or {}
        if score is None or score.priority_score < int(filters.get("minimum_priority_score", 65)):
            return False, "lead does not meet campaign score filter", None, None
        source_ids = filters.get("source_ids")
        if source_ids and str(lead.source_id) not in {str(value) for value in source_ids}:
            return False, "lead source does not match campaign audience", None, None
        contact = uow.contacts.get(tenant_id=tenant_id, contact_id=lead.contact_id)
        if contact is None or contact.business_id != business_id:
            return False, "contact is not provisioned in campaign business", None, None
        business = uow.businesses.get(tenant_id=tenant_id, business_id=business_id)
        tenant = uow.tenants.get(tenant_id=tenant_id)
        decision = self._policy_service.evaluate(
            uow, tenant=tenant, business=business, contact=contact,
            lead_id=lead.id, channel=campaign.channel, direction="outbound",
        )
        if not decision.allowed:
            reason = ("contact consent is not granted"
                      if decision.reason_code.startswith("consent_")
                      else "contact or lead is suppressed"
                      if decision.reason_code == "suppressed"
                      else decision.reason_code)
            return False, reason, contact, None
        enrichment = uow.lead_enrichments.latest(tenant_id=tenant_id, business_id=business_id, lead_id=lead.id)
        return True, "eligible", contact, enrichment

    @staticmethod
    def _render(template: dict[str, Any], *, contact: Any, business: Any, enrichment: Any) -> dict[str, str]:
        company_info = enrichment.company_info if enrichment is not None else {}
        replacements = {
            "contact_name": contact.full_name or "there",
            "business_name": business.name,
            "company_name": company_info.get("name", "your company"),
        }
        token = re.compile(r"{{\s*(contact_name|business_name|company_name)\s*}}")
        result = {}
        for field in ("subject", "body"):
            value = template.get(field)
            if value is not None:
                result[field] = token.sub(lambda match: replacements[match.group(1)], value)
        return result

    @staticmethod
    def _snapshot(row: Any) -> Any:
        return type(row)(**row.model_dump())


__all__ = ["CampaignEligibilityError", "EnrollmentService"]
