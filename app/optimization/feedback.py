"""Idempotent, PII-minimized feedback capture for optimization learning."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID
from app.optimization.segments import segment_for_lead


_FORBIDDEN_KEYS = re.compile(r"(^|_)(name|phone|email|address|message|text|body|content|recipient|token|secret)(_|$)", re.I)
_ALLOWED_EVENTS = {
    "lead_scored", "appointment_booked", "appointment_no_show", "reply_received",
    "opted_out", "contact_escalated", "contact_resolved_without_escalation",
    "appointment_proposed", "action_recommended", "action_taken", "manual_feedback",
    "campaign_step_sent", "message_opened",
    "feature_flag_exposure",
}


class FeedbackEventCapture:
    def capture(self, *, uow, tenant_id: str, business_id: str, event_key: str,
                correlation_id: str, event_type: str, outcome: str,
                decision: dict[str, Any] | None = None,
                event_metadata: dict[str, Any] | None = None,
                lead_id: UUID | None = None, contact_id: str | None = None,
                campaign_id: UUID | None = None,
                occurred_at: datetime | None = None):
        if event_type not in _ALLOWED_EVENTS:
            raise ValueError("unsupported optimization feedback event")
        if not event_key.strip() or len(event_key) > 200:
            raise ValueError("feedback event key must contain 1-200 characters")
        if not correlation_id.strip() or len(correlation_id) > 100:
            raise ValueError("feedback correlation ID must contain 1-100 characters")
        decision = self._safe_object(decision or {})
        event_metadata = self._safe_object(event_metadata or {})
        return uow.optimization.add_feedback(
            tenant_id=tenant_id, business_id=business_id, event_key=event_key,
            correlation_id=correlation_id, event_type=event_type, outcome=outcome[:64],
            decision=decision, event_metadata=event_metadata,
            lead_id=lead_id, contact_id=contact_id, campaign_id=campaign_id,
            occurred_at=(occurred_at or datetime.now(UTC)),
        )

    def replay_persisted_event(self, *, uow, event) -> bool:
        """Backfill supported durable events through the same idempotent writer."""
        if not event.tenant_id or not event.business_id:
            return False
        correlation_id = event.correlation_id or str(event.id)
        tenant_id, business_id = event.tenant_id, event.business_id
        payload = event.payload or {}
        if event.event_type == "appointment.confirmed" and payload.get("appointment_id"):
            appointment_id = UUID(str(payload["appointment_id"]))
            appointment = uow.appointments.get_for_tenant(
                tenant_id=tenant_id, business_id=business_id,
                appointment_id=appointment_id,
            )
            if appointment is None:
                return False
            self.capture(
                uow=uow, tenant_id=tenant_id, business_id=business_id,
                event_key=f"appointment-booked:{appointment.id}",
                correlation_id=correlation_id, event_type="appointment_booked",
                outcome="booked", contact_id=appointment.contact_id,
                lead_id=None, campaign_id=appointment.campaign_id,
                decision={"source": appointment.source, "campaign_id": str(appointment.campaign_id or ""),
                          "staff_id": str(appointment.staff_id or ""),
                          "slot_hour": appointment.starts_at.hour},
                event_metadata={"backfilled": True}, occurred_at=event.created_at,
            )
            return True
        if event.event_type == "human.escalation.alert" and payload.get("ticket_id"):
            ticket = uow.escalations.get_in_tenant(
                tenant_id=tenant_id, ticket_id=UUID(str(payload["ticket_id"])),
            )
            if ticket is None:
                return False
            self.capture(
                uow=uow, tenant_id=tenant_id, business_id=business_id,
                event_key=f"escalation-created:{ticket.id}",
                correlation_id=correlation_id, event_type="contact_escalated",
                outcome="escalated", contact_id=ticket.contact_id,
                decision={"ticket_id": str(ticket.id), "priority": ticket.priority,
                          "reason_code": ticket.reason[:120]},
                event_metadata={"backfilled": True}, occurred_at=event.created_at,
            )
            return True
        if event.event_type == "outbound.delivery.sent" and payload.get("message_id"):
            run = uow.campaign_step_runs.get_by_message(
                tenant_id=tenant_id, business_id=business_id,
                message_id=UUID(str(payload["message_id"])),
            )
            if run is None:
                return False
            enrollment = uow.campaign_enrollments.get_by_id(
                tenant_id=tenant_id, business_id=business_id,
                enrollment_id=run.enrollment_id,
            )
            if enrollment is None:
                return False
            sequences = uow.campaign_sequences.list_for_campaign(
                tenant_id=tenant_id, business_id=business_id,
                campaign_id=enrollment.campaign_id,
            )
            sequence = next((item for item in sequences if item.id == enrollment.sequence_id), None)
            segment = segment_for_lead(
                uow, tenant_id=tenant_id, business_id=business_id,
                lead_id=enrollment.lead_id,
            )
            self.capture(
                uow=uow, tenant_id=tenant_id, business_id=business_id,
                event_key=f"campaign-step-sent:{run.id}",
                correlation_id=correlation_id, event_type="campaign_step_sent",
                outcome="sent", lead_id=enrollment.lead_id,
                campaign_id=enrollment.campaign_id,
                decision={"variant": sequence.name if sequence else "unknown",
                          "test_group": enrollment.experiment_group,
                          "step": run.step_order,
                          "sent_hour": run.sent_at.hour if run.sent_at else event.created_at.hour,
                          "segment": segment},
                event_metadata={"backfilled": True}, occurred_at=event.created_at,
            )
            return True
        return False

    def replay_campaign_event(self, *, uow, event) -> bool:
        payload = event.payload or {}
        enrollment_id = event.enrollment_id
        enrollment = (uow.campaign_enrollments.get_by_id(
            tenant_id=event.tenant_id, business_id=event.business_id,
            enrollment_id=enrollment_id,
        ) if enrollment_id else None)
        lead = (uow.leads.get(tenant_id=event.tenant_id, business_id=event.business_id,
                              lead_id=enrollment.lead_id) if enrollment else None)
        if event.event_type == "campaign.reply.received" and payload.get("reply_id"):
            reply = uow.campaign_replies.get_by_id(
                tenant_id=event.tenant_id, business_id=event.business_id,
                reply_id=UUID(str(payload["reply_id"])),
            )
            if reply is None or lead is None:
                return False
            sequences = uow.campaign_sequences.list_for_campaign(
                tenant_id=event.tenant_id, business_id=event.business_id,
                campaign_id=event.campaign_id,
            )
            sequence = next((item for item in sequences if item.id == enrollment.sequence_id), None)
            self.capture(
                uow=uow, tenant_id=event.tenant_id, business_id=event.business_id,
                event_key=f"campaign-reply:{reply.id}",
                correlation_id=payload.get("correlation_id") or
                    f"campaign:{event.campaign_id}:enrollment:{enrollment.id}",
                event_type="reply_received", outcome=reply.classification,
                lead_id=lead.id, campaign_id=event.campaign_id,
                decision={"variant": sequence.name if sequence else "unknown",
                          "test_group": enrollment.experiment_group,
                          "step": enrollment.current_step_order,
                          "state": "lead_contacted_no_reply"},
                event_metadata={"sentiment": reply.classification, "backfilled": True},
                occurred_at=event.created_at,
            )
            return True
        if event.event_type == "campaign.opted_out" and enrollment is not None:
            self.capture(
                uow=uow, tenant_id=event.tenant_id, business_id=event.business_id,
                event_key=f"campaign-optout:{enrollment.id}",
                correlation_id=f"campaign:{event.campaign_id}:enrollment:{enrollment.id}",
                event_type="opted_out", outcome="opt_out", lead_id=lead.id if lead else None,
                campaign_id=event.campaign_id,
                decision={"channel": payload.get("channel", "unknown"),
                          "step": enrollment.current_step_order},
                event_metadata={"backfilled": True}, occurred_at=event.created_at,
            )
            return True
        return False

    def replay_provider_event(self, *, uow, event) -> int:
        """Backfill SendGrid opens from authenticated, durable webhook events."""
        payload = event.payload or {}
        rows = payload.get("events", []) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            return 0
        captured = 0
        for index, item in enumerate(rows[:500]):
            if not isinstance(item, dict) or str(item.get("event", "")).casefold() != "open":
                continue
            custom_args = item.get("custom_args", {})
            message_id = (custom_args.get("message_id") if isinstance(custom_args, dict) else None)
            message_id = message_id or item.get("message_id")
            try:
                parsed_message_id = UUID(str(message_id))
            except (TypeError, ValueError):
                continue
            message = uow.messages.get_in_tenant(
                tenant_id=event.tenant_id, message_id=parsed_message_id,
            )
            if message is None or message.channel != "email":
                continue
            run = uow.campaign_step_runs.get_by_message(
                tenant_id=event.tenant_id, business_id=event.business_id,
                message_id=parsed_message_id,
            )
            if run is None:
                continue
            enrollment = uow.campaign_enrollments.get_by_id(
                tenant_id=event.tenant_id, business_id=event.business_id,
                enrollment_id=run.enrollment_id,
            )
            if enrollment is None:
                continue
            sequence = next((row for row in uow.campaign_sequences.list_for_campaign(
                tenant_id=event.tenant_id, business_id=event.business_id,
                campaign_id=enrollment.campaign_id,
            ) if row.id == enrollment.sequence_id), None)
            segment = segment_for_lead(
                uow, tenant_id=event.tenant_id, business_id=event.business_id,
                lead_id=enrollment.lead_id,
            )
            self.capture(
                uow=uow, tenant_id=event.tenant_id, business_id=event.business_id,
                event_key=f"email-open:{event.id}:{index}",
                correlation_id=(f"campaign:{enrollment.campaign_id}:enrollment:{enrollment.id}"),
                event_type="message_opened", outcome="opened",
                lead_id=enrollment.lead_id, campaign_id=enrollment.campaign_id,
                decision={"variant": sequence.name if sequence else "unknown",
                          "test_group": getattr(enrollment, "experiment_group", "all"),
                          "step": run.step_order,
                          "sent_hour": run.sent_at.hour if run.sent_at else event.created_at.hour,
                          "segment": segment},
                event_metadata={"channel": "email"}, occurred_at=event.created_at,
            )
            captured += 1
        return captured

    @classmethod
    def _safe_object(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("feedback decision and metadata must be objects")
        def clean(item, depth=0):
            if depth > 4:
                raise ValueError("feedback object nesting exceeds limit")
            if isinstance(item, dict):
                result = {}
                for key, child in item.items():
                    if (not isinstance(key, str) or key.casefold() in {"contact", "contact_id"}
                            or _FORBIDDEN_KEYS.search(key)):
                        raise ValueError("feedback metadata cannot contain PII fields")
                    result[key[:80]] = clean(child, depth + 1)
                return result
            if isinstance(item, (list, tuple)):
                if len(item) > 100:
                    raise ValueError("feedback list exceeds limit")
                return [clean(child, depth + 1) for child in item]
            if item is None or isinstance(item, (bool, int, float, str)):
                if isinstance(item, str) and len(item) > 500:
                    raise ValueError("feedback text values must be bounded")
                return item
            raise ValueError("feedback metadata must be JSON-compatible")
        result = clean(value)
        encoded = json.dumps(result, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 8192:
            raise ValueError("feedback object exceeds 8 KiB")
        return result
