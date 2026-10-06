"""Backward-compatible in-memory dispatcher retained for Phase 0 callers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.agents import ReceptionistAgent
from app.config import settings
from app.events import EventEnvelope, InMemoryEventBus
from app.policies import PolicyChecker
from app.workflows import WorkflowEngine


class TaskDispatcher:
    def __init__(self):
        self.workflow_engine = WorkflowEngine()
        self.policy_checker = PolicyChecker()
        self.event_bus = InMemoryEventBus()
        self.receptionist_agent = ReceptionistAgent()

    def handle_inbound(self, payload: dict[str, Any]) -> dict[str, Any]:
        conversation_id = payload["contact_id"]
        tenant_id = payload["tenant_id"]
        business_id = payload["business_id"]
        channel = payload["channel"]
        # This compatibility dispatcher has no provisioned consent record.
        # Ignore caller consent and region metadata.
        consent_status = "unknown"
        region = settings.default_region
        workflow = self.workflow_engine.create_workflow(conversation_id, tenant_id, business_id)
        self.event_bus.publish(EventEnvelope(
            event_id=f"evt-{datetime.utcnow().timestamp()}", tenant_id=tenant_id,
            business_id=business_id, conversation_id=conversation_id,
            event_type="inbound.contact.received", payload=payload, source="api",
            correlation_id=conversation_id,
        ))
        ok, reason = self.policy_checker.validate(region, channel, consent_status)
        if not ok:
            workflow = self.workflow_engine.update_state(
                conversation_id, "blocked", {"reason": reason},
            )
            return {"success": False, "status": "blocked",
                    "workflow_state": workflow["state"], "reason": reason}
        workflow = self.workflow_engine.update_state(conversation_id, "intent_classified", {
            "intent": "appointment_inquiry", "channel": channel,
        })
        agent_result = self.receptionist_agent.run({
            "contact_name": payload.get("contact_name", "there"),
            "intent": "appointment inquiry",
        })
        self.event_bus.publish(EventEnvelope(
            event_id=f"evt-{datetime.utcnow().timestamp()}", tenant_id=tenant_id,
            business_id=business_id, conversation_id=conversation_id,
            event_type="agent.response.generated", payload=agent_result,
            source="receptionist_agent", correlation_id=conversation_id,
        ))
        return {"success": True, "status": "ok",
                "workflow_state": workflow["state"],
                "agent_response": agent_result["response"]}
