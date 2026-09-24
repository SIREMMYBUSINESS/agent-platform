"""Availability lookup, appointment proposals, and confirmations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.persistence.models import Appointment
from app.services.routing_service import RoutingService


@dataclass(frozen=True)
class SchedulingResult:
    appointment: Appointment
    response: str


class SchedulingService:
    """Schedules only from provisioned active services, staff, and slots."""

    def __init__(self, routing_service: RoutingService | None = None) -> None:
        self._routing = routing_service or RoutingService()

    @staticmethod
    def is_confirmation(text: str) -> bool:
        normalized = text.casefold().strip()
        return any(
            phrase in normalized
            for phrase in ("confirm", "yes", "that works", "book it", "accept the time")
        )

    def propose(
        self,
        *,
        uow: Any,
        tenant_id: str,
        business_id: str,
        contact_id: str,
        conversation_id,
        text: str,
        now: datetime,
        contact_timezone: str | None,
    ) -> SchedulingResult | None:
        services = uow.services.list_active(tenant_id=tenant_id, business_id=business_id)
        staff = uow.staff.list_active(tenant_id=tenant_id, business_id=business_id)
        if not services or not staff:
            return None

        normalized = text.casefold()
        matching = [service for service in services if service.name.casefold() in normalized]
        service = matching[0] if len(matching) == 1 else services[0] if len(services) == 1 else None
        if service is None:
            return None

        available = uow.appointment_slots.list_available(
            tenant_id=tenant_id, business_id=business_id, after=now
        )
        choices = [slot for slot in available if slot.service_id == service.id]
        if not choices:
            return None

        selected_slot = None
        for candidate in choices:
            selected_slot = uow.appointment_slots.reserve(
                tenant_id=tenant_id, slot_id=candidate.id
            )
            if selected_slot is not None:
                break
        if selected_slot is None:
            return None

        appointment = uow.appointments.create(
            tenant_id=tenant_id,
            business_id=business_id,
            contact_id=contact_id,
            conversation_id=conversation_id,
            staff_id=selected_slot.staff_id,
            service_id=selected_slot.service_id,
            slot_id=selected_slot.id,
            starts_at=selected_slot.starts_at,
            ends_at=selected_slot.ends_at,
            status="proposed",
        )
        display_timezone = contact_timezone or "UTC"
        selected_time = self._routing.format_local_time(selected_slot.starts_at, display_timezone)
        alternate_times = [
            self._routing.format_local_time(slot.starts_at, display_timezone)
            for slot in choices
            if slot.id != selected_slot.id
        ][:2]
        if alternate_times:
            options = "; other options are " + " or ".join(alternate_times)
        else:
            options = ""
        return SchedulingResult(
            appointment=appointment,
            response=(
                f"I can schedule your {service.name} for {selected_time}{options}. "
                "Reply yes to confirm or tell me what time works better."
            ),
        )

    def confirm(
        self,
        *,
        uow: Any,
        tenant_id: str,
        business_id: str,
        contact_id: str,
        contact_timezone: str | None,
    ) -> SchedulingResult | None:
        proposed = uow.appointments.latest_proposed(
            tenant_id=tenant_id, business_id=business_id, contact_id=contact_id
        )
        if proposed is None:
            return None
        confirmed = uow.appointments.confirm(
            tenant_id=tenant_id, appointment_id=proposed.id
        )
        if confirmed is None:
            return None
        local_time = self._routing.format_local_time(
            confirmed.starts_at, contact_timezone or "UTC"
        )
        return SchedulingResult(
            appointment=confirmed,
            response=f"Your appointment is confirmed for {local_time}.",
        )


__all__ = ["SchedulingResult", "SchedulingService"]
