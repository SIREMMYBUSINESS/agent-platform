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
        return asdict(self)


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
        "greeting": "Hello {{contact_name}}, thanks for contacting {{clinic_name}}.",
        "ask_reason": "What would you like help scheduling?",
        "propose_slots": "Available times for {{service_type}}: {{slot_1}}, {{slot_2}}, {{slot_3}}. Reply with your choice.",
        "confirmation": "Your {{service_type}} appointment is confirmed for {{appointment_time}} at {{clinic_name}}.",
        "reminder": "Reminder: your appointment at {{clinic_name}} is {{appointment_time}}.",
        "follow_up": "Thanks for visiting {{clinic_name}}. How was your appointment?",
        "outreach": "Hello {{contact_name}}, it may be time to schedule a visit with {{clinic_name}}. Reply BOOK to see available times or STOP to opt out.",
    },
    escalation_rules={
        "immediate_staff_review": ["breathing difficulty", "uncontrolled bleeding", "facial swelling", "serious injury"],
        "clinical_boundary": ["diagnosis request", "medication advice", "treatment advice"],
    },
    success_metrics=("booking_rate", "no_show_rate", "customer_satisfaction"),
    required_providers=("sms", "calendar"),
    optional_providers=("email", "crm"),
)

_TEMPLATES = {DENTAL_CLINIC.vertical_id: DENTAL_CLINIC}


def list_vertical_templates() -> list[VerticalTemplate]:
    return list(_TEMPLATES.values())


def get_vertical_template(vertical_id: str) -> VerticalTemplate:
    try:
        return _TEMPLATES[vertical_id]
    except KeyError:
        raise ValueError("unsupported business vertical") from None


def render_message(template: str, values: dict[str, str]) -> str:
    """Substitute only known, supplied placeholders; reject unknown variables."""
    allowed = {"contact_name", "clinic_name", "service_type", "slot_1", "slot_2",
               "slot_3", "appointment_time", "reason", "insurance_provider"}
    fields = set(re.findall(r"{{\s*([a-z][a-z0-9_]*)\s*}}", template))
    residue = re.sub(r"{{\s*[a-z][a-z0-9_]*\s*}}", "", template)
    if "{{" in residue or "}}" in residue:
        raise ValueError("message template has malformed variable syntax")
    if fields - allowed or fields - set(values):
        raise ValueError("message template contains an unsupported or unresolved variable")
    return re.sub(r"{{\s*([a-z][a-z0-9_]*)\s*}}",
                  lambda match: str(values[match.group(1)]), template)


__all__ = ["IntakeQuestion", "ServiceTemplate", "VerticalTemplate", "DENTAL_CLINIC",
           "get_vertical_template", "list_vertical_templates", "render_message"]
