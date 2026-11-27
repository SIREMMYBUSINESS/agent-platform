"""Validated vertical defaults. Tenant onboarding stores a copy before customization."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re


@dataclass(frozen=True)
class IntakeQuestion:
    key: str
    prompt: str
    answer_type: str = "text"
    required: bool = True


@dataclass(frozen=True)
class ServiceTemplate:
    name: str
    duration_minutes: int


@dataclass(frozen=True)
class VerticalTemplate:
    vertical_id: str
    display_name: str
    intake_questions: tuple[IntakeQuestion, ...]
    business_hours: tuple[dict, ...]
    services: tuple[ServiceTemplate, ...]
    message_templates: dict[str, str]
    escalation_rules: dict[str, list[str]]
    success_metrics: tuple[str, ...]
    required_providers: tuple[str, ...]
    optional_providers: tuple[str, ...]

    def as_config(self) -> dict:
        config = asdict(self)
        config["template_variables_by_key"] = {
            key: sorted(extract_template_variables(value))
            for key, value in self.message_templates.items()
        }
        return config


DENTAL_CLINIC = VerticalTemplate(
    vertical_id="healthcare_dental",
    display_name="Dental clinic",
    intake_questions=(
        IntakeQuestion("service_type", "What dental service would you like to book?"),
        IntakeQuestion("reason", "What should the clinic know about your visit?"),
        IntakeQuestion("insurance_provider", "Do you have a dental insurance provider? You may skip this." , required=False),
        IntakeQuestion("preferred_date", "What date or day works best for you?", required=False),
    ),
    business_hours=tuple(
        {"weekday": day, "opens_at": "08:00", "closes_at": "17:00", "active": True}
        for day in range(5)
    ) + ({"weekday": 5, "opens_at": "09:00", "closes_at": "13:00", "active": True},
         {"weekday": 6, "opens_at": "09:00", "closes_at": "13:00", "active": False}),
    services=(
        ServiceTemplate("Dental cleaning", 60),
        ServiceTemplate("Dental consultation", 30),
        ServiceTemplate("Filling", 60),
        ServiceTemplate("Urgent dental visit", 30),
    ),
    message_templates={
        "greeting": "Hello {{contact_name}}, thanks for contacting our team.",
        "ask_reason": "What would you like help scheduling?",
        "propose_slots": "Available times for {{service_type}}: {{slot_1}}, {{slot_2}}, {{slot_3}}. Reply with your choice.",
        "confirmation": "Your {{service_type}} appointment is confirmed for {{appointment_time}} at {{clinic_name}}.",
        "reminder": "Reminder: your appointment at {{clinic_name}} is {{appointment_time}}.",
        "follow_up": "Thanks for visiting {{clinic_name}}. How was your appointment?",
        "outreach": "Hello {{contact_name}}, we're following up on your enquiry. Reply to continue.",
    },
    escalation_rules={
        "immediate_staff_review": ["breathing difficulty", "uncontrolled bleeding", "facial swelling", "serious injury"],
        "clinical_boundary": ["diagnosis request", "medication advice", "treatment advice"],
    },
    success_metrics=("booking_rate", "no_show_rate", "customer_satisfaction"),
    required_providers=("sms", "calendar"),
    optional_providers=("email", "crm"),
)


def _general_template(vertical_id: str, display_name: str, service_names: tuple[str, ...],
                      intake_label: str) -> VerticalTemplate:
    """Create a neutral front-desk template without importing dental clinical policy."""
    questions = (
        IntakeQuestion("service_type", f"What {intake_label} are you interested in?"),
        IntakeQuestion("reason", "What would you like our team to know?"),
        IntakeQuestion("preferred_date", "What date or time works best for you?", required=False),
    )
    return VerticalTemplate(
        vertical_id=vertical_id,
        display_name=display_name,
        intake_questions=questions,
        business_hours=tuple(
            {"weekday": day, "opens_at": "09:00", "closes_at": "17:00", "active": day < 5}
            for day in range(7)
        ),
        services=tuple(ServiceTemplate(name, 30) for name in service_names),
        message_templates={
            "greeting": "Hello {{contact_name}}, thanks for contacting our team.",
            "ask_reason": "How can our team help you today?",
            "propose_slots": "Available times for {{service_type}}: {{slot_1}}, {{slot_2}}, {{slot_3}}. Reply with your choice.",
            "confirmation": "Your {{service_type}} consultation is confirmed for {{appointment_time}}.",
            "reminder": "Reminder: your appointment is {{appointment_time}}.",
            "follow_up": "Thanks for contacting our team. How can we help further?",
            "outreach": "Hello {{contact_name}}, we're following up on your enquiry. Reply to continue.",
        },
        escalation_rules={},
        success_metrics=("response_rate", "booking_rate", "customer_satisfaction"),
        required_providers=("sms", "calendar"),
        optional_providers=("email", "crm"),
    )


REAL_ESTATE = _general_template(
    "real_estate", "Real estate", ("Property viewing", "Buyer consultation", "Rental enquiry"),
    "property or service",
)
MEDICAL_CLINIC = _general_template(
    "healthcare_medical", "Medical clinic", ("General consultation", "Follow-up visit", "Clinic enquiry"),
    "appointment or service",
)
LEGAL_SERVICES = _general_template(
    "legal_services", "Legal services", ("Initial consultation", "Case review", "Document consultation"),
    "legal service",
)
HOME_SERVICES = _general_template(
    "home_services", "Home services", ("Inspection", "Repair visit", "Installation"),
    "home service",
)
AUTOMOTIVE = _general_template(
    "automotive", "Automotive", ("Vehicle service", "Diagnostic appointment", "Repair consultation"),
    "vehicle service",
)
HOSPITALITY = _general_template(
    "hospitality", "Hospitality", ("Reservation enquiry", "Guest assistance", "Event enquiry"),
    "booking or service",
)
RETAIL = _general_template(
    "retail", "Retail", ("Product enquiry", "Store consultation", "Order assistance"),
    "product or service",
)

_TEMPLATES = {
    template.vertical_id: template
    for template in (
        REAL_ESTATE, DENTAL_CLINIC, MEDICAL_CLINIC, LEGAL_SERVICES,
        HOME_SERVICES, AUTOMOTIVE, HOSPITALITY, RETAIL,
    )
}


def list_vertical_templates() -> list[VerticalTemplate]:
    return list(_TEMPLATES.values())


def get_vertical_template(vertical_id: str) -> VerticalTemplate:
    try:
        return _TEMPLATES[vertical_id]
    except KeyError:
        raise ValueError("unsupported business vertical") from None


def extract_template_variables(template: str) -> set[str]:
    """Return syntactically valid variable names present in one template."""
    return set(re.findall(r"{{\s*([A-Za-z][A-Za-z0-9_]*)\s*}}", template))


def render_message(template: str, values: dict[str, str], *,
                   allowed_variables: set[str] | None = None) -> str:
    """Purely substitute supplied variables, enforcing one selected template's contract."""
    allowed = ({"contact_name", "clinic_name", "service_type", "slot_1", "slot_2",
                "slot_3", "appointment_time", "reason", "insurance_provider"}
               if allowed_variables is None else set(allowed_variables))
    fields = extract_template_variables(template)
    residue = re.sub(r"{{\s*[A-Za-z][A-Za-z0-9_]*\s*}}", "", template)
    if "{{" in residue or "}}" in residue:
        raise ValueError("malformed template variable syntax")
    unsupported = fields - allowed
    if unsupported:
        names = ", ".join("{{" + name + "}}" for name in sorted(unsupported))
        raise ValueError(f"unsupported template variable(s): {names}")
    unresolved = fields - set(values)
    if unresolved:
        names = ", ".join("{{" + name + "}}" for name in sorted(unresolved))
        raise ValueError(f"unresolved template variable(s): {names}")
    return re.sub(r"{{\s*([A-Za-z][A-Za-z0-9_]*)\s*}}",
                  lambda match: str(values[match.group(1)]), template)


def resolve_stored_message_template(vertical_id: str, message_key: str,
                                    stored_template: str) -> tuple[str, str | None]:
    """Use saved copy only when it matches the selected vertical's own default contract."""
    defaults = get_vertical_template(vertical_id).message_templates
    if message_key not in defaults:
        raise ValueError("unsupported message template key")
    default_template = defaults[message_key]
    allowed = extract_template_variables(default_template)
    try:
        if not isinstance(stored_template, str):
            raise ValueError("stored template is not text")
        render_message(stored_template, {name: name for name in allowed},
                       allowed_variables=allowed)
        if message_key == "greeting" and "our team" not in stored_template:
            raise ValueError("greeting is missing the business-name marker 'our team'")
    except ValueError as exc:
        return default_template, (
            f"Saved {message_key} template is incompatible with {vertical_id}: {exc}. "
            f"The {vertical_id} default is being used instead."
        )
    return stored_template, None


def render_business_message(template: str, business, contact, staff, *,
                            message_key: str, allowed_variables: set[str]) -> str:
    """Pure renderer: read the supplied records and return text; never persists or sends."""
    contact_name = getattr(contact, "full_name", None)
    if not isinstance(contact_name, str) or not contact_name.strip():
        raise ValueError("contact record has no display name")
    rendered = render_message(
        template, {"contact_name": contact_name}, allowed_variables=allowed_variables,
    )
    if message_key == "greeting":
        business_name = getattr(business, "name", None)
        if not isinstance(business_name, str) or not business_name.strip():
            raise ValueError("business record has no name")
        if "our team" not in rendered:
            raise ValueError("greeting is missing the business-name marker 'our team'")
        return rendered.replace("our team", business_name.strip())
    if message_key == "outreach":
        staff_name = getattr(staff, "display_name", None)
        if not isinstance(staff_name, str) or not staff_name.strip():
            raise ValueError("staff record has no display name")
        return f"{rendered.rstrip()} — {staff_name.strip()}"
    raise ValueError("unsupported record-backed message key")


__all__ = ["IntakeQuestion", "ServiceTemplate", "VerticalTemplate", "DENTAL_CLINIC",
           "extract_template_variables", "get_vertical_template", "list_vertical_templates",
           "render_business_message", "render_message", "resolve_stored_message_template"]
