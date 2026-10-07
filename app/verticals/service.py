"""Tenant-safe dental onboarding and persistent sequential intake."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.persistence.models import Business, RoutingRule, Service, Staff
from app.security import AuthenticatedPrincipal
from app.services.escalation_service import EscalationService
from app.services.outbound_delivery_service import OutboundDeliveryService
from app.services.policy_service import PolicyService
from app.services.scheduling_service import SchedulingService
from app.services.campaign_service import CampaignService
from app.services.enrollment_service import EnrollmentService
from app.schemas import (CampaignCreateRequest, CampaignEnrollRequest,
                         CampaignMessageTemplate, CampaignStepInput)
from app.services.receptionist_intake_service import ReceptionistIntakeService
from app.verticals.schemas import DentalOnboardingRequest
from app.verticals.templates import get_vertical_template, render_message


class VerticalAuthorizationError(PermissionError):
    pass


class VerticalProvisioningError(LookupError):
    pass


_PROVIDER_OPTIONS = {
    "sms": {"twilio"}, "email": {"sendgrid", "smtp"},
    "calendar": {"google"}, "crm": {"hubspot", "salesforce"},
}
_TEMPLATE_KEYS = {"greeting", "ask_reason", "propose_slots", "confirmation",
                  "reminder", "follow_up", "outreach"}
_ALLOWED_TEMPLATE_VARIABLES = {
    "contact_name", "clinic_name", "service_type", "slot_1", "slot_2",
    "slot_3", "appointment_time", "reason", "insurance_provider",
}


class VerticalOnboardingService:
    def __init__(self, *, uow_factory, provider_config_resolver=None,
                 scheduling_service: SchedulingService | None = None,
                 now_provider=None):
        self._uow_factory = uow_factory
        self._provider_configs = provider_config_resolver
        self._scheduling = scheduling_service or SchedulingService()
        self._now = now_provider or (lambda: datetime.now(UTC))
        self._outbound = OutboundDeliveryService()
        self._escalations = EscalationService()
        self._policy = PolicyService()

    @staticmethod
    def _require(principal: AuthenticatedPrincipal, role: str) -> None:
        if not isinstance(principal, AuthenticatedPrincipal) or role not in principal.roles:
            raise VerticalAuthorizationError("vertical operation denied")

    def catalog(self, principal: AuthenticatedPrincipal) -> list[dict]:
        self._require(principal, "business_onboarder")
        template = get_vertical_template("healthcare_dental")
        return [{"vertical_id": template.vertical_id,
                 "display_name": template.display_name,
                 "required_providers": list(template.required_providers),
                 "optional_providers": list(template.optional_providers)}]

    def template(self, principal: AuthenticatedPrincipal, vertical_id: str) -> dict:
        self._require(principal, "business_onboarder")
        return get_vertical_template(vertical_id).as_config()

    def onboard(self, principal: AuthenticatedPrincipal,
                request: DentalOnboardingRequest) -> dict:
        self._require(principal, "business_onboarder")
        template = get_vertical_template(request.vertical_id)
        for kind, provider in request.provider_selection.items():
            if kind not in _PROVIDER_OPTIONS or provider not in _PROVIDER_OPTIONS[kind]:
                raise ValueError("unsupported provider selection")
        if set(request.message_templates) - _TEMPLATE_KEYS:
            raise ValueError("unsupported message template key")
        messages = {**template.message_templates, **request.message_templates}
        for value in messages.values():
            render_message(value, {key: key for key in _ALLOWED_TEMPLATE_VARIABLES})
        questions = ([question.model_dump() for question in request.intake_questions]
                     if request.intake_questions is not None else
                     [item.__dict__ for item in template.intake_questions])
        services = ([item.model_dump() for item in request.services]
                    if request.services is not None else
                    [item.__dict__ for item in template.services])
        hours = ([item.model_dump(mode="json") for item in request.business_hours]
                 if request.business_hours is not None else list(template.business_hours))
        escalation = {key: list(value) for key, value in template.escalation_rules.items()}
        for key, phrases in request.escalation_rules.items():
            if len(key) > 64 or len(phrases) > 40 or any(not phrase.strip() or len(phrase) > 120 for phrase in phrases):
                raise ValueError("invalid escalation rule")
            escalation[key] = sorted(set(escalation.get(key, []) + phrases))

        with self._uow_factory() as uow:
            tenant = uow.tenants.get(tenant_id=principal.tenant_id)
            if tenant is None:
                raise VerticalProvisioningError("tenant not found")
            region_id = request.region_id or tenant.default_region_id
            if region_id not in set(tenant.supported_region_ids or []):
                raise ValueError("business region must be enabled for this tenant")
            region = uow.regions.get(tenant_id=tenant.id, region_id=region_id)
            if region is None:
                raise ValueError("business region is not approved")
            timezone_name = request.timezone or region.timezone
            try:
                ZoneInfo(timezone_name)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError("timezone must be a valid IANA timezone") from None
            business_id = str(uuid4())
            business = uow.businesses.add(Business(
                id=business_id, tenant_id=tenant.id,
                name=request.business_name.strip(), vertical=template.vertical_id,
                region=region.compliance_profile.get("region_group", region.country_code),
                country_code=region.country_code, region_id=region_id,
                timezone=timezone_name, default_language=request.language or region.language,
            ))
            config = {
                "intake_questions": questions,
                "services": services,
                "message_templates": messages,
                "escalation_rules": escalation,
                "success_metrics": list(template.success_metrics),
                "provider_selection": request.provider_selection,
                "required_providers": list(template.required_providers),
                "optional_providers": list(template.optional_providers),
            }
            profile = uow.verticals.put_profile(
                tenant_id=tenant.id, business_id=business.id,
                vertical_id=template.vertical_id, config=config,
                created_by=principal.principal_id,
            )
            for item in services:
                uow.services.add(Service(
                    tenant_id=tenant.id, business_id=business.id,
                    name=item["name"].strip(), duration_minutes=item["duration_minutes"],
                ))
            for item in hours:
                uow.routing_rules.add(RoutingRule(
                    tenant_id=tenant.id, business_id=business.id,
                    weekday=item["weekday"], opens_at=_parse_time(item["opens_at"]),
                    closes_at=_parse_time(item["closes_at"]), active=item["active"],
                    after_hours_action="callback",
                ))
            for member in request.staff:
                uow.staff.add(Staff(tenant_id=tenant.id, business_id=business.id,
                                    display_name=member.display_name.strip(), email=member.email))
            uow.regions.add_audit(
                tenant_id=tenant.id, region_id=region_id,
                action="vertical_onboarding_created", decision="allow",
                resource_type="business", resource_id=business.id,
                actor_id=principal.principal_id,
                details={"vertical_id": template.vertical_id,
                         "status": profile.status,
                         "service_count": len(services), "staff_count": len(request.staff)},
            )
            return {"tenant_id": tenant.id, "business_id": business.id,
                    "vertical_id": profile.vertical_id, "status": profile.status,
                    "region_id": region_id, "timezone": timezone_name}

    def health_check(self, principal: AuthenticatedPrincipal, *, business_id: str) -> dict:
        self._require(principal, "business_onboarder")
        with self._uow_factory() as uow:
            business = uow.businesses.get(tenant_id=principal.tenant_id,
                                          business_id=business_id)
            profile = uow.verticals.profile(tenant_id=principal.tenant_id,
                                             business_id=business_id)
            if business is None or profile is None:
                raise VerticalProvisioningError("business setup was not found")
            region = uow.regions.get(tenant_id=principal.tenant_id,
                                     region_id=business.region_id)
            checks = {
                "business": True,
                "region_approved": bool(region and region.active
                                        and region.approval_status == "approved"),
                "country_policy": self._country_policy_ready(uow, region),
                "services": bool(uow.services.list_active(tenant_id=principal.tenant_id,
                                                           business_id=business_id)),
                "business_hours": len(uow.routing_rules.list_for_business(
                    tenant_id=principal.tenant_id, business_id=business_id)) >= 1,
                "staff": bool(uow.staff.list_active(tenant_id=principal.tenant_id,
                                                    business_id=business_id)),
            }
            setup_checks_ready = all(checks.values())
            providers = self._provider_checks(
                tenant_id=principal.tenant_id, business_id=business_id,
                region_id=business.region_id, config=profile.config,
            )
            checks.update({f"provider_{key}": value for key, value in providers.items()})
            required = set(profile.config.get("required_providers", []))
            required_ready = all(providers.get(kind, False) for kind in required)
            ready = setup_checks_ready and required_ready
            return {"tenant_id": principal.tenant_id, "business_id": business_id,
                    "vertical_id": profile.vertical_id, "status": profile.status,
                    "ready": ready, "checks": checks,
                    "provider_status": {key: "configured" if value else "missing"
                                        for key, value in providers.items()}}

    @staticmethod
    def _country_policy_ready(uow, region) -> bool:
        if region is None or region.compliance_profile.get("safe_mode"):
            return False
        country_policy = uow.country_policies.get(country_code=region.country_code)
        return bool(country_policy and country_policy.active
                    and not country_policy.rules.get("safe_mode"))

    def _provider_checks(self, *, tenant_id, business_id, region_id, config) -> dict[str, bool]:
        selected = config.get("provider_selection", {})
        results = {}
        if self._provider_configs is None:
            return {kind: False for kind in set(selected) | set(config.get("required_providers", []))}
        try:
            values = dict(self._provider_configs.resolve(tenant_id=tenant_id,
                                                         business_id=business_id))
        except Exception:
            return {kind: False for kind in set(selected) | set(config.get("required_providers", []))}
        if region_id:
            suffix = "__" + region_id.replace("-", "_").upper()
            for key, value in tuple(values.items()):
                if key.upper().endswith(suffix):
                    values[key[:-len(suffix)]] = value
        credentials = {
            "sms": ("account_sid", "auth_token"),
            "email": ("api_key", "from_address"),
            "calendar": ("access_token", "calendar_id"),
        }
        for kind in set(selected) | set(config.get("required_providers", [])):
            selector = selected.get(kind)
            configured_selector = values.get(f"{kind}_provider", selector)
            required_keys = credentials.get(kind, ())
            configured = bool(selector == configured_selector and required_keys
                              and all(values.get(key, "").strip() for key in required_keys))
            if kind == "sms":
                configured = configured and bool(values.get("from_sms", "").strip()
                                                  or values.get("messaging_service_sid", "").strip())
            elif kind == "email":
                if selector == "smtp":
                    configured = bool(selector == configured_selector
                        and values.get("host", "").strip()
                        and values.get("from_address", "").strip())
                else:
                    configured = bool(selector == configured_selector
                        and values.get("api_key", "").strip()
                        and values.get("from_address", "").strip())
            elif kind == "crm":
                configured = bool(selector == configured_selector and (
                    values.get("access_token", "").strip()
                    or values.get("client_id", "").strip()
                ))
            results[kind] = configured
        return results

    def activate(self, principal: AuthenticatedPrincipal, *, business_id: str) -> dict:
        self._require(principal, "business_onboarder")
        health = self.health_check(principal, business_id=business_id)
        if not health["ready"]:
            raise ValueError("business setup must pass health checks before go-live")
        with self._uow_factory() as uow:
            profile = uow.verticals.set_status(
                tenant_id=principal.tenant_id, business_id=business_id, status="live",
            )
            if profile is None:
                raise VerticalProvisioningError("business setup was not found")
            region_id = uow.businesses.get(tenant_id=principal.tenant_id,
                                          business_id=business_id).region_id
            uow.regions.add_audit(
                tenant_id=principal.tenant_id, region_id=region_id,
                action="vertical_onboarding_live", decision="allow",
                resource_type="business", resource_id=business_id,
                actor_id=principal.principal_id,
                details={"vertical_id": profile.vertical_id, "checks_passed": True},
            )
            return {"business_id": business_id, "status": profile.status,
                    "vertical_id": profile.vertical_id}

    def start_intake(self, principal: AuthenticatedPrincipal, *, business_id: str,
                     contact_id: str, channel: str, idempotency_key: str) -> dict:
        self._require(principal, "workflow_executor")
        with self._uow_factory() as uow:
            business, tenant, contact, profile = self._scope(
                uow, principal.tenant_id, business_id, contact_id,
            )
            prior = uow.verticals.intake_by_key(
                tenant_id=principal.tenant_id, business_id=business_id,
                idempotency_key=idempotency_key,
            )
            if prior is not None:
                return self._intake_view(prior, profile.config)
            conversation = uow.conversations.create(
                tenant_id=principal.tenant_id, business_id=business_id,
                contact_id=contact_id, channel=channel,
                correlation_id=f"vertical:{idempotency_key}",
            )
            session = uow.verticals.create_intake(
                tenant_id=principal.tenant_id, business_id=business_id,
                contact_id=contact_id, conversation_id=conversation.id,
                idempotency_key=idempotency_key,
            )
            uow.events.add(tenant_id=principal.tenant_id, business_id=business_id,
                conversation_id=conversation.id, event_type="vertical.intake.started",
                payload={"vertical_id": profile.vertical_id}, source="vertical_intake",
                correlation_id=conversation.correlation_id)
            prompt = self._question(profile.config, 0)
            self._send_question(uow, tenant, business, contact, conversation,
                                session.id, 0, prompt, channel)
            return self._intake_view(session, profile.config)

    def answer_intake(self, principal: AuthenticatedPrincipal, *, business_id: str,
                      session_id: UUID, answer: str, idempotency_key: str) -> dict:
        self._require(principal, "workflow_executor")
        with self._uow_factory() as uow:
            tenant = uow.tenants.get(tenant_id=principal.tenant_id)
            business = uow.businesses.get(tenant_id=principal.tenant_id,
                                          business_id=business_id)
            profile = uow.verticals.profile(tenant_id=principal.tenant_id,
                                             business_id=business_id)
            session = uow.verticals.intake(
                tenant_id=principal.tenant_id, business_id=business_id,
                session_id=session_id, lock=True,
            )
            if session is None:
                raise VerticalProvisioningError("intake session not found")
            contact = uow.contacts.get(tenant_id=principal.tenant_id,
                                       contact_id=session.contact_id)
            if (tenant is None or business is None or profile is None or contact is None
                    or contact.business_id != business.id):
                raise VerticalProvisioningError("vertical intake scope is unavailable")
            previous_digest = session.answer_idempotency.get(idempotency_key)
            if previous_digest:
                from hashlib import sha256
                if previous_digest != sha256(answer.encode()).hexdigest():
                    raise ValueError("answer idempotency key was reused with different content")
                return self._intake_view(session, profile.config)
            if session.status != "collecting":
                return self._intake_view(session, profile.config)
            questions = profile.config["intake_questions"]
            question = questions[session.question_index]
            conversation = uow.conversations.get(
                tenant_id=principal.tenant_id, conversation_id=session.conversation_id,
            )
            uow.messages.add(tenant_id=principal.tenant_id,
                conversation_id=conversation.id, sender="contact", channel=conversation.channel,
                content=answer, direction="inbound")
            next_index = session.question_index + 1
            clean_answer = " ".join(answer.split())
            if not question.get("required", True) and not clean_answer:
                clean_answer = ""
            session = uow.verticals.save_answer(
                tenant_id=principal.tenant_id, business_id=business_id,
                session_id=session_id, answer_key=question["key"],
                question_index=next_index, answer=clean_answer, status="collecting",
                idempotency_key=idempotency_key,
            )
            uow.events.add(tenant_id=principal.tenant_id, business_id=business_id,
                conversation_id=conversation.id, event_type="vertical.intake.answer_recorded",
                payload={"question_key": question["key"], "question_index": next_index},
                source="vertical_intake", correlation_id=conversation.correlation_id)
            if next_index < len(questions):
                next_question = self._question(profile.config, next_index)
                self._send_question(uow, tenant, business, contact, conversation,
                                    session.id, next_index, next_question, conversation.channel)
                return self._intake_view(session, profile.config)
            return self._finish_intake(uow, tenant, business, contact, conversation,
                                       profile, session, answer)

    @staticmethod
    def _scope(uow, tenant_id, business_id, contact_id):
        tenant = uow.tenants.get(tenant_id=tenant_id)
        business = uow.businesses.get(tenant_id=tenant_id, business_id=business_id)
        profile = uow.verticals.profile(tenant_id=tenant_id, business_id=business_id)
        contact = (uow.contacts.get(tenant_id=tenant_id, contact_id=contact_id)
                   if contact_id else None)
        if tenant is None or business is None or profile is None or (
                contact_id and (contact is None or contact.business_id != business.id)):
            raise VerticalProvisioningError("vertical intake scope is unavailable")
        return business, tenant, contact, profile

    @staticmethod
    def _question(config, index):
        question = config["intake_questions"][index]
        return {"question_key": question["key"], "prompt": question["prompt"]}

    def _send_question(self, uow, tenant, business, contact, conversation,
                       session_id, question_index, question, channel):
        template = uow.verticals.profile(
            tenant_id=tenant.id, business_id=business.id).config["message_templates"]
        greeting = template["greeting"] if question_index == 0 else ""
        content = " ".join(part for part in (greeting, question["prompt"]) if part)
        self._outbound.queue(uow, tenant=tenant, business=business, contact=contact,
            conversation=conversation, channel=channel, content=content,
            message_id=uuid5(NAMESPACE_URL, f"vertical-intake:{session_id}:question:{question_index}"),
            correlation_id=conversation.correlation_id)

    def _finish_intake(self, uow, tenant, business, contact, conversation,
                       profile, session, source_answer):
        answers = dict(session.answers)
        rules = profile.config.get("escalation_rules", {})
        normalized = " ".join(answers.get("reason", "").casefold().split())
        critical = next((label for label, terms in rules.items()
                         if any(term.casefold() in normalized for term in terms)), None)
        if critical:
            ticket = self._escalations.create_ticket(
                uow=uow, tenant_id=tenant.id, business_id=business.id,
                contact_id=contact.id, conversation_id=conversation.id,
                reason=f"dental_intake:{critical}", priority="urgent",
            )
            session = uow.verticals.save_answer(
                tenant_id=tenant.id, business_id=business.id, session_id=session.id,
                answer_key="__complete__", question_index=session.question_index,
                answer="true", status="escalated", idempotency_key=f"complete:{session.id}",
            )
            uow.events.add(tenant_id=tenant.id, business_id=business.id,
                conversation_id=conversation.id, event_type="vertical.intake.escalated",
                payload={"ticket_id": str(ticket.id), "rule": critical},
                source="vertical_intake", correlation_id=conversation.correlation_id)
            return {"session_id": session.id, "status": "escalated",
                    "escalation_id": ticket.id}
        service_type = answers.get("service_type", "")
        preferred = answers.get("preferred_date", "")
        classification = ReceptionistIntakeService().classify(
            f"book {service_type} {preferred}",
            service_names=tuple(item["name"] for item in profile.config.get("services", [])),
        )
        result = self._scheduling.propose(
            uow=uow, tenant_id=tenant.id, business_id=business.id,
            contact_id=contact.id, conversation_id=conversation.id,
            text=f"appointment {service_type}", now=self._now(),
            contact_timezone=contact.timezone or business.timezone,
            preferred_date=classification.entities.get("date_preference") or _parse_date(preferred),
            preferred_weekday=classification.entities.get("weekday_preference"),
            preferred_time=classification.entities.get("time_preference"),
        )
        if result is None:
            ticket = self._escalations.create_ticket(
                uow=uow, tenant_id=tenant.id, business_id=business.id,
                contact_id=contact.id, conversation_id=conversation.id,
                reason="dental_intake_no_available_appointment", priority="normal",
            )
            uow.verticals.save_answer(tenant_id=tenant.id, business_id=business.id,
                session_id=session.id, answer_key="__complete__",
                question_index=session.question_index, answer="true", status="escalated",
                idempotency_key=f"complete:{session.id}")
            return {"session_id": session.id, "status": "escalated",
                    "escalation_id": ticket.id}
        uow.verticals.save_answer(tenant_id=tenant.id, business_id=business.id,
            session_id=session.id, answer_key="__complete__",
            question_index=session.question_index, answer="true", status="appointment_proposed",
            idempotency_key=f"complete:{session.id}")
        message = self._outbound.queue(
            uow, tenant=tenant, business=business, contact=contact,
            conversation=conversation, channel=conversation.channel,
            content=result.response,
            message_id=uuid5(NAMESPACE_URL, f"vertical-intake:{session.id}:proposal"),
            correlation_id=conversation.correlation_id)
        uow.events.add(tenant_id=tenant.id, business_id=business.id,
            conversation_id=conversation.id, event_type="vertical.intake.appointment_proposed",
            payload={"appointment_id": str(result.appointment.id),
                     "delivery_status": message.delivery_status},
            source="vertical_intake", correlation_id=conversation.correlation_id)
        return {"session_id": session.id, "status": "appointment_proposed",
                "appointment_id": result.appointment.id, "response": result.response}

    @staticmethod
    def _intake_view(session, config):
        if session.question_index < len(config["intake_questions"]):
            question = config["intake_questions"][session.question_index]
            return {"session_id": session.id, "status": session.status,
                    "question_key": question["key"], "prompt": question["prompt"]}
        return {"session_id": session.id, "status": session.status}


def _parse_time(value):
    return value if hasattr(value, "hour") else datetime.strptime(value, "%H:%M:%S" if len(value) == 8 else "%H:%M").time()


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date().isoformat()
    except ValueError:
        return None


__all__ = ["VerticalAuthorizationError", "VerticalProvisioningError", "VerticalOnboardingService"]
