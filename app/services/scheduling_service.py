"""Availability lookup, appointment proposals, and confirmations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
import re
from typing import Any

from app.persistence.models import Appointment
from app.services.routing_service import RoutingService
from app.services.scheduling_optimization import SchedulingOptimizationService
from app.services.appointment_reminder_service import AppointmentReminderService
from app.services.crm_sync_service import CRMSyncService
from app.services.contact_action_tokens import ContactActionTokens
from app.observability.metrics import metrics
from app.optimization.feedback import FeedbackEventCapture
from app.optimization.tracker import OptimizationTracker


@dataclass(frozen=True)
class SchedulingResult:
    appointment: Appointment
    response: str
    reproposed: bool = False


class SchedulingService:
    """Schedules only from provisioned active services, staff, and slots."""

    def __init__(self, routing_service: RoutingService | None = None,
                 optimizer: SchedulingOptimizationService | None = None,
                 calendar_provider=None, public_app_url: str = "http://localhost:3000") -> None:
        self._routing = routing_service or RoutingService()
        self._optimizer = optimizer or SchedulingOptimizationService()
        self._reminders = AppointmentReminderService()
        self._crm = CRMSyncService()
        self._feedback = FeedbackEventCapture()
        self._optimization_tracker = OptimizationTracker(self._feedback)
        self._calendar = calendar_provider
        self._public_app_url = public_app_url.rstrip("/")

    @staticmethod
    def selection_index(text: str) -> int | None:
        normalized = " ".join(text.casefold().split())
        if normalized in {"1", "1st", "option 1", "first", "the first"}:
            return 0
        if normalized in {"2", "2nd", "option 2", "second", "the second"}:
            return 1
        if normalized in {"3", "3rd", "option 3", "third", "the third"}:
            return 2
        match = re.fullmatch(r"(?:option|slot)\s+([1-3])", normalized)
        return int(match.group(1)) - 1 if match else None

    @staticmethod
    def is_confirmation(text: str) -> bool:
        normalized = text.casefold().strip()
        return SchedulingService.selection_index(normalized) is not None or any(
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
        source: str = "inbound",
        campaign_id=None,
        campaign_enrollment_id=None,
        preferred_date: str | None = None,
        preferred_weekday: int | None = None,
        preferred_time: list[str] | tuple[str, str] | None = None,
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
        parsed_preferred_date = date.fromisoformat(preferred_date) if preferred_date else None
        parsed_preferred_time = (
            (time.fromisoformat(preferred_time[0]), time.fromisoformat(preferred_time[1]))
            if preferred_time else None
        )
        choices = self._optimizer.rank_slots(
            [slot for slot in available if slot.service_id == service.id],
            now=now, timezone_name=contact_timezone,
            preferred_date=parsed_preferred_date,
            preferred_weekday=preferred_weekday,
            preferred_time=parsed_preferred_time,
        )
        if choices and self._calendar is not None:
            earliest = min(self._as_utc(slot.starts_at) for slot in choices)
            latest = max(self._as_utc(slot.ends_at) for slot in choices)
            events = self._calendar.availability(
                tenant_id=tenant_id, business_id=business_id,
                time_min=earliest.isoformat(), time_max=latest.isoformat(),
            )
            busy = [(self._event_time(event, "start"), self._event_time(event, "end"))
                    for event in events]
            choices = [slot for slot in choices if not any(
                self._as_utc(slot.starts_at) < end and self._as_utc(slot.ends_at) > start
                for start, end in busy
            )]
        if not choices:
            return None

        choices = choices[:3]
        action_token, action_token_hash = ContactActionTokens.issue()

        selected_slot = uow.appointment_slots.reserve(
            tenant_id=tenant_id, slot_id=choices[0].id
        )
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
            source=source,
            campaign_id=campaign_id,
            campaign_enrollment_id=campaign_enrollment_id,
            proposal_slot_ids=[str(slot.id) for slot in choices],
            contact_action_token_hash=action_token_hash,
            correlation_id=(getattr(uow.conversations.get(
                tenant_id=tenant_id, conversation_id=conversation_id,
            ), "correlation_id", None) or str(conversation_id)),
        )
        action_lead_id = None
        if campaign_enrollment_id is not None:
            enrollment = uow.campaign_enrollments.get_by_id(
                tenant_id=tenant_id, business_id=business_id,
                enrollment_id=campaign_enrollment_id,
            )
            action_lead_id = enrollment.lead_id if enrollment else None
        self._feedback.capture(
            uow=uow, tenant_id=tenant_id, business_id=business_id,
            event_key=f"appointment-proposed:{appointment.id}",
            correlation_id=appointment.correlation_id or str(appointment.id),
            event_type="appointment_proposed", outcome="proposed",
            campaign_id=campaign_id,
            decision={"source": source, "slot_hour": self._as_utc(appointment.starts_at).hour,
                      "staff_id": str(appointment.staff_id or "")},
            event_metadata={"service_id": str(appointment.service_id or "")},
        )
        self._feedback.capture(
            uow=uow, tenant_id=tenant_id, business_id=business_id,
            event_key=f"action-taken:appointment-proposed:{appointment.id}",
            correlation_id=appointment.correlation_id or str(appointment.id),
            event_type="action_taken", outcome="appointment_proposed",
            lead_id=action_lead_id, campaign_id=campaign_id,
            decision={"action": "propose_appointment",
                      "state": "lead_replied_positive" if source == "outreach" else "new_lead"},
        )
        display_timezone = contact_timezone or "UTC"
        option_text = "; ".join(
            f"{index}) {self._routing.format_local_time(slot.starts_at, display_timezone)}"
            for index, slot in enumerate(choices, start=1)
        )
        metrics.appointment_proposal(
            source=source if source in {"inbound", "outreach"} else "other",
            tenant_id=str(tenant_id), business_id=str(business_id),
        )
        return SchedulingResult(
            appointment=appointment,
            response=(
                f"I can schedule your {service.name} at: {option_text}. "
                "Reply with the number of your preferred time, or choose online: "
                f"{self._public_app_url}/appointments/action?token={action_token}"
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
        now: datetime | None = None,
        appointment_id=None,
        selection_text: str | None = None,
    ) -> SchedulingResult | None:
        proposed = (uow.appointments.get_for_tenant(
            tenant_id=tenant_id, business_id=business_id, appointment_id=appointment_id,
        ) if appointment_id is not None else uow.appointments.latest_proposed(
            tenant_id=tenant_id, business_id=business_id, contact_id=contact_id,
        ))
        if proposed is None or proposed.status != "proposed" or proposed.contact_id != contact_id:
            return None
        selection = self.selection_index(selection_text or "")
        selection_conflict = False
        if selection_text and selection is not None:
            offered = proposed.proposal_slot_ids or []
            if selection >= len(offered):
                selection_conflict = True
            elif proposed.slot_id is None or str(proposed.slot_id) != offered[selection]:
                selected = uow.appointments.select_proposed_slot(
                    tenant_id=tenant_id, business_id=business_id,
                    appointment_id=proposed.id, slot_id=offered[selection],
                )
                if selected is None:
                    selection_conflict = True
                else:
                    proposed = selected
        calendar_conflict = selection_conflict
        if self._calendar is not None and not calendar_conflict:
            events = self._calendar.availability(
                tenant_id=tenant_id, business_id=business_id,
                time_min=self._as_utc(proposed.starts_at).isoformat(),
                time_max=self._as_utc(proposed.ends_at).isoformat(),
            )
            calendar_conflict = any(
                self._as_utc(proposed.starts_at) < self._event_time(event, "end")
                and self._as_utc(proposed.ends_at) > self._event_time(event, "start")
                for event in events
            )
        if calendar_conflict:
            old_id, old_slot = proposed.id, proposed.slot_id
            service = next((item for item in uow.services.list_active(
                tenant_id=tenant_id, business_id=business_id,
            ) if item.id == proposed.service_id), None)
            if old_slot is not None:
                uow.appointment_slots.release_reservation(
                    tenant_id=tenant_id, slot_id=old_slot,
                )
            uow.appointments.mark_conflict(tenant_id=tenant_id, appointment_id=old_id)
            alternative = self.propose(
                uow=uow, tenant_id=tenant_id, business_id=business_id,
                contact_id=contact_id, conversation_id=proposed.conversation_id,
                text=service.name if service else "",
                now=self._as_utc(now or datetime.now(timezone.utc)),
                contact_timezone=contact_timezone,
                source=proposed.source, campaign_id=proposed.campaign_id,
                campaign_enrollment_id=proposed.campaign_enrollment_id,
            )
            if alternative is None:
                return None
            return SchedulingResult(
                appointment=alternative.appointment,
                response=("That time is no longer available. " + alternative.response),
                reproposed=True,
            )
        confirmed = uow.appointments.confirm(
            tenant_id=tenant_id, appointment_id=proposed.id
        )
        if confirmed is None:
            return None
        metrics.appointment_booking(
            source=confirmed.source if confirmed.source in {"inbound", "outreach"} else "other",
            tenant_id=str(tenant_id), business_id=str(business_id),
        )
        confirmed.correlation_id = confirmed.correlation_id or str(
            confirmed.conversation_id or confirmed.id
        )
        lead = None
        lead_score = None
        variant = None
        if confirmed.campaign_id and confirmed.campaign_enrollment_id:
            enrollment = uow.campaign_enrollments.get(
                tenant_id=tenant_id, business_id=business_id,
                campaign_id=confirmed.campaign_id,
                enrollment_id=confirmed.campaign_enrollment_id,
            )
            if enrollment is not None:
                lead = uow.leads.get(tenant_id=tenant_id, business_id=business_id,
                                     lead_id=enrollment.lead_id)
                score = uow.lead_scores.latest(tenant_id=tenant_id,
                                               business_id=business_id, lead_id=enrollment.lead_id)
                lead_score = ([score.fit_score, score.urgency_score, score.intent_score]
                              if score else [])
                sequences = uow.campaign_sequences.list_for_campaign(
                    tenant_id=tenant_id, business_id=business_id,
                    campaign_id=confirmed.campaign_id,
                )
                sequence = next((item for item in sequences if item.id == enrollment.sequence_id), None)
                variant = sequence.name if sequence else None
        conversion_now = self._as_utc(now or datetime.now(timezone.utc))
        lead_created = self._as_utc(lead.created_at) if lead and lead.created_at else conversion_now
        conversion_days = max(0, (conversion_now - lead_created).days) if lead else 0
        self._optimization_tracker.record_outcome(
            uow=uow, tenant_id=tenant_id, business_id=business_id,
            event_key=f"appointment-booked:{confirmed.id}",
            correlation_id=confirmed.correlation_id,
            event_type="appointment_booked", outcome="booked",
            lead_id=lead.id if lead else None, campaign_id=confirmed.campaign_id,
            decision={"source": confirmed.source, "variant": variant,
                      "campaign_id": str(confirmed.campaign_id or ""),
                      "staff_id": str(getattr(confirmed, "staff_id", None) or ""),
                      "slot_hour": self._as_utc(confirmed.starts_at).hour},
            event_metadata={"lead_score_at_contact": lead_score,
                            "time_to_conversion_days": conversion_days},
            occurred_at=now or datetime.now(timezone.utc),
        )
        self._reminders.schedule(uow, appointment=confirmed)
        self._crm.enqueue_appointment_activity(
            uow, appointment=confirmed,
            activity_type=f"{confirmed.source}_appointment",
        )
        if confirmed.campaign_id and confirmed.campaign_enrollment_id:
            enrollment = uow.campaign_enrollments.get(
                tenant_id=tenant_id, business_id=business_id,
                campaign_id=confirmed.campaign_id,
                enrollment_id=confirmed.campaign_enrollment_id,
            )
            uow.campaign_events.add(
                tenant_id=tenant_id, business_id=business_id,
                campaign_id=confirmed.campaign_id,
                enrollment_id=confirmed.campaign_enrollment_id,
                event_type="campaign.appointment.booked",
                payload={"appointment_id": str(confirmed.id)},
            )
            lead = (uow.leads.get(
                tenant_id=tenant_id, business_id=business_id, lead_id=enrollment.lead_id,
            ) if enrollment is not None else None)
            if lead is not None and lead.status == "responded":
                uow.lead_transitions.transition(
                    tenant_id=tenant_id, business_id=business_id, lead_id=lead.id,
                    expected_state="responded", new_state="booked",
                    context={"source": "appointment_confirmation",
                             "appointment_id": str(confirmed.id)},
                )
        local_time = self._routing.format_local_time(
            confirmed.starts_at, contact_timezone or "UTC"
        )
        return SchedulingResult(
            appointment=confirmed,
            response=f"Your appointment is confirmed for {local_time}.",
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @classmethod
    def _event_time(cls, event, edge: str) -> datetime:
        raw = event.get(edge) if isinstance(event, dict) else None
        value = raw.get("dateTime") or raw.get("date") if isinstance(raw, dict) else None
        if not value:
            raise ValueError("calendar returned an event without a valid time")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return cls._as_utc(parsed)


__all__ = ["SchedulingResult", "SchedulingService"]
