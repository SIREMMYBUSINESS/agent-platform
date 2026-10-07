"""Region-aware policy decisions with fail-closed defaults and audit logging."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.services.consent_service import ConsentService
from app.services.suppression_service import SuppressionService
from app.observability.metrics import metrics
from app.observability.telemetry import start_span
from opentelemetry import trace
import logging


logger = logging.getLogger("app.observability")


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason_code: str
    country_code: str
    consent_status: str
    policy_source: str
    overridden: bool = False


@dataclass(frozen=True)
class NextBestActionDecision:
    action: str
    reason_code: str
    allowed: bool


def _channel_rule(consent_required: bool = True) -> dict[str, bool]:
    return {"allowed": True, "consent_required": consent_required}


DEFAULT_COUNTRY_POLICIES = {
    "US": {
        "region_group": "US",
        "rules": {
            "inbound": {"chat": _channel_rule(False), "email": _channel_rule(),
                        "sms": _channel_rule(), "call": _channel_rule(),
                        "phone": _channel_rule(), "voicemail": _channel_rule()},
            "outbound": {"chat": _channel_rule(False), "email": _channel_rule(), "sms": _channel_rule(),
                         "whatsapp": _channel_rule(), "call": _channel_rule()},
        },
    },
    "CA": {
        "region_group": "CA",
        "rules": {
            "inbound": {"email": _channel_rule(), "sms": _channel_rule(),
                        "chat": _channel_rule(False), "call": _channel_rule()},
            "outbound": {"chat": _channel_rule(False), "email": _channel_rule(), "sms": _channel_rule(),
                         "whatsapp": _channel_rule()},
        },
    },
    "EU": {
        "region_group": "EU",
        "rules": {
            "inbound": {"email": _channel_rule(), "sms": _channel_rule(),
                        "chat": _channel_rule(), "call": _channel_rule()},
            "outbound": {"chat": _channel_rule(False), "email": _channel_rule(), "sms": _channel_rule(),
                         "whatsapp": _channel_rule()},
        },
    },
    # Africa is not treated as one legal regime. Until a trusted, country-level
    # CountryPolicy is provisioned, use a safe mode that blocks processing.
    "AFRICA": {
        "region_group": "AFRICA",
        "safe_mode": True,
        "rules": {"inbound": {}, "outbound": {}},
    },
}


class PolicyService:
    def __init__(self, *, consent_service: ConsentService | None = None,
                 suppression_service: SuppressionService | None = None,
                 now_provider=None) -> None:
        self._consent = consent_service or ConsentService()
        self._suppression = suppression_service or SuppressionService()
        self._now = now_provider or (lambda: datetime.now(UTC))

    def evaluate(self, uow, *, tenant, business, contact, channel: str,
                 direction: str, lead_id: UUID | None = None,
                 override_actor: str | None = None,
                 override_reason: str | None = None) -> PolicyDecision:
        with start_span("policy.evaluate", attributes={
            "policy.channel": channel, "policy.direction": direction,
            "tenant.id": str(tenant.id), "business.id": str(business.id),
        }):
            decision = self._evaluate(
                uow, tenant=tenant, business=business, contact=contact,
                channel=channel, direction=direction, lead_id=lead_id,
                override_actor=override_actor, override_reason=override_reason,
            )
            trace.get_current_span().set_attribute("policy.allowed", decision.allowed)
            trace.get_current_span().set_attribute("policy.reason_code", decision.reason_code)
            return decision

    def _evaluate(self, uow, *, tenant, business, contact, channel: str,
                  direction: str, lead_id: UUID | None = None,
                  override_actor: str | None = None,
                  override_reason: str | None = None) -> PolicyDecision:
        tenant_id, business_id = tenant.id, business.id
        country_code = (getattr(business, "country_code", None)
                        or business.region or tenant.region or "").strip().upper()
        now = self._now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        consent_status = self._consent.status(
            uow, tenant_id=tenant_id, business_id=business_id,
            contact=contact, channel=channel, now=now,
        )
        profile = uow.policy_profiles.get(tenant_id=tenant_id, business_id=business_id)
        suppression = self._suppression.is_suppressed(
            uow, tenant_id=tenant_id, business_id=business_id,
            contact_id=contact.id, lead_id=lead_id, channel=channel,
        )

        country_policy = uow.country_policies.get(country_code=country_code)
        region_group = (business.region or tenant.region or "").strip().upper()
        business_country = getattr(business, "country_code", None)
        may_use_group_default = not business_country or country_code == region_group
        if (country_policy is None and may_use_group_default
                and region_group in {"US", "CA", "EU", "AFRICA"}):
            country_policy = uow.country_policies.get(country_code=region_group)
        country_policy_inactive = country_policy is not None and not country_policy.active
        if country_policy is not None and country_policy.active:
            policy_data = {"region_group": country_policy.region_group,
                           "rules": country_policy.rules}
            source = f"country_policy:{country_policy.country_code}"
        elif country_policy_inactive:
            policy_data = None
            source = f"country_policy_inactive:{country_code}"
        else:
            policy_data = DEFAULT_COUNTRY_POLICIES.get(country_code)
            fallback_code = country_code
            if policy_data is None and may_use_group_default:
                policy_data = DEFAULT_COUNTRY_POLICIES.get(region_group)
                fallback_code = region_group
            source = f"safe_default:{fallback_code}" if policy_data else "safe_default:UNKNOWN"

        if direction not in {"inbound", "outbound"}:
            raise ValueError("direction must be inbound or outbound")

        allowed = False
        reason = "unknown_region"
        hard_compliance_block = False
        custom_block = False
        if suppression is not None:
            reason = "suppressed"
            hard_compliance_block = True
        elif consent_status in {"revoked", "expired"}:
            reason = f"consent_{consent_status}"
            hard_compliance_block = True
        elif country_policy_inactive:
            reason = "country_policy_inactive"
            hard_compliance_block = True
        elif policy_data is not None:
            if profile is not None and profile.mode not in {"enforce", "safe_mode"}:
                reason = "invalid_policy_profile"
                hard_compliance_block = True
            elif profile is not None and profile.mode == "safe_mode":
                reason = "profile_safe_mode"
                hard_compliance_block = True
            elif policy_data.get("safe_mode"):
                reason = "country_specific_policy_required"
                hard_compliance_block = True
            else:
                country_rules = policy_data.get("rules")
                direction_rules = (country_rules.get(direction)
                                   if isinstance(country_rules, dict) else None)
                region_rule = (direction_rules.get(channel)
                               if isinstance(direction_rules, dict) else None)
                if not isinstance(region_rule, dict):
                    reason = "channel_not_allowed_by_region_policy"
                    hard_compliance_block = True
                elif region_rule.get("allowed") is not True:
                    reason = "channel_not_allowed_by_region_policy"
                    hard_compliance_block = True
                elif region_rule.get("consent_required", True) and consent_status != "granted":
                    reason = f"consent_{consent_status}"
                    hard_compliance_block = True
                else:
                    allowed, reason = True, "policy_allowed"

                # Business profiles can only add restrictions. Their blocks
                # can be overridden only where trusted configuration permits;
                # statutory gates, opt-outs, and suppressions remain absolute.
                profile_rules: Any = profile.custom_rules if profile else {}
                if profile is not None and not isinstance(profile_rules, dict):
                    allowed, reason, hard_compliance_block = False, "invalid_business_policy", True
                custom_direction = (profile_rules.get(direction)
                                    if isinstance(profile_rules, dict) else None)
                custom = (custom_direction.get(channel)
                          if isinstance(custom_direction, dict) else {})
                if allowed and not isinstance(custom, dict):
                    allowed, reason, hard_compliance_block = False, "invalid_business_policy", True
                elif allowed and custom.get("allowed") is False:
                    allowed, reason, custom_block = False, "business_policy_block", True
                if allowed and custom.get("consent_required") is True and consent_status != "granted":
                    allowed, reason, custom_block = False, f"consent_{consent_status}", True

        override_attempted = override_actor is not None or override_reason is not None
        overridden = False
        if (not allowed and custom_block and not hard_compliance_block
                and profile is not None and profile.allow_manual_override
                and override_actor and override_reason and consent_status == "granted"):
            allowed, reason, overridden = True, "manual_override", True

        if override_attempted and not overridden:
            if not override_reason or not override_actor:
                reason = f"{reason}; override requires actor and reason"

        metrics.policy_decision(channel=channel if channel in {
            "sms", "email", "voice", "call", "chat", "voicemail", "whatsapp", "unknown",
        } else "unknown", direction=direction, allowed=allowed,
            tenant_id=str(tenant_id), business_id=str(business_id))
        uow.policy_decisions.add(
            tenant_id=tenant_id, business_id=business_id,
            direction=direction, channel=channel, country_code=country_code or "UNKNOWN",
            resource_type="lead" if lead_id is not None else "contact",
            resource_id=str(lead_id or contact.id),
            decision="allow" if allowed else "block", reason_code=reason,
            consent_status=consent_status, policy_source=source,
            overridden=overridden,
            override_actor=override_actor if override_attempted else None,
            override_reason=override_reason if override_attempted else None,
        )
        logger.info("policy decision", extra={
            "tenant_id": str(tenant_id), "business_id": str(business_id),
            "component": "workflow", "operation": "policy_decision",
            "outcome": "allowed" if allowed else "blocked",
        })
        return PolicyDecision(allowed, reason, country_code, consent_status,
                              source, overridden)

    @staticmethod
    def next_best_action(*, priority_score: int, urgency_score: int,
                         outreach_allowed: bool, contactable: bool,
                         business_open: bool, appointment_available: bool,
                         campaign_capacity: bool, allowed_channels: tuple[str, ...]) -> NextBestActionDecision:
        """Select from trusted policy and persisted business constraints."""
        if urgency_score >= 85:
            return NextBestActionDecision("human_escalation", "urgent_lead", True)
        if not contactable:
            return NextBestActionDecision("manual_review", "contact_not_verified", False)
        if not outreach_allowed:
            return NextBestActionDecision("policy_blocked", "outbound_policy_block", False)
        if not business_open:
            return NextBestActionDecision("schedule_follow_up", "business_closed", True)
        if appointment_available and business_open and priority_score >= 75:
            return NextBestActionDecision("offer_appointment", "high_priority_slot_available", True)
        if campaign_capacity and allowed_channels and priority_score >= 65:
            return NextBestActionDecision("outreach_now", "qualified_and_within_capacity", True)
        if campaign_capacity and allowed_channels:
            return NextBestActionDecision("nurture", "below_qualification_threshold", True)
        return NextBestActionDecision("schedule_follow_up", "business_capacity_unavailable", True)


__all__ = ["DEFAULT_COUNTRY_POLICIES", "NextBestActionDecision", "PolicyDecision", "PolicyService"]
