"""Deterministic receptionist intent and appointment entity extraction."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
import re

from app.services.scheduling_service import SchedulingService


@dataclass(frozen=True)
class ReceptionistIntakeResult:
    intent: str
    priority: str
    entities: dict


class ReceptionistIntakeService:
    WEEKDAYS = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }
    REASON_PATTERN = re.compile(r"\b(?:because|reason is)\s+([^.!?]{1,240})", re.I)
    DATE_PATTERN = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")

    def classify(self, text: str, *, service_names: tuple[str, ...] = ()) -> ReceptionistIntakeResult:
        normalized = " ".join(text.casefold().split())
        if any(term in normalized for term in (
            "urgent", "emergency", "asap", "immediately", "severe pain", "can't breathe",
            "cannot breathe", "fire", "unsafe", "threat", "bleeding",
        )):
            intent, priority = "urgent", "urgent"
        elif SchedulingService.is_confirmation(text):
            intent, priority = "appointment_confirmation", "routine"
        elif any(term in normalized for term in ("appointment", "schedule", "booking", "book", "reschedule")):
            intent, priority = "appointment", "routine"
        elif any(term in normalized for term in ("business hours", "opening hours", "open", "close")):
            intent, priority = "business_hours", "routine"
        elif "?" in text or normalized.startswith((
            "what ", "when ", "where ", "who ", "how ", "why ", "do you ", "can you ",
        )):
            intent, priority = "question", "routine"
        else:
            intent, priority = "unknown", "routine"

        entities: dict = {}
        date_match = self.DATE_PATTERN.search(text)
        if date_match:
            try:
                entities["date_preference"] = date.fromisoformat(date_match.group(1)).isoformat()
            except ValueError:
                pass
        for day, weekday in self.WEEKDAYS.items():
            if re.search(rf"\b{day}\b", normalized):
                entities["weekday_preference"] = weekday
                break
        for label, bounds in (
            ("morning", (time(6), time(12))),
            ("afternoon", (time(12), time(17))),
            ("evening", (time(17), time(22))),
        ):
            if label in normalized:
                entities["time_preference"] = [bound.isoformat() for bound in bounds]
                break
        matched_service = next((name for name in service_names if name.casefold() in normalized), None)
        if matched_service:
            entities["service_type"] = matched_service
        reason_match = self.REASON_PATTERN.search(text)
        if reason_match:
            reason = " ".join(reason_match.group(1).split())
            entities["reason"] = re.sub(r"^of\s+", "", reason, flags=re.I)[:240]
        return ReceptionistIntakeResult(intent, priority, entities)


__all__ = ["ReceptionistIntakeResult", "ReceptionistIntakeService"]
