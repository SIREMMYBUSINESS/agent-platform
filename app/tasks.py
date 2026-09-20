from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from app.agents import ReceptionistAgent
from app.events import EventEnvelope, InMemoryEventBus
from app.policies import PolicyChecker
from app.workflows import WorkflowEngine

class TaskDispatcher:
    def __init__(self):
        self.workflow_engine = WorkflowEngine()
        self.policy_checker = PolicyChecker()
        self.event_bus = InMemoryEventBus()
        self.receptionist_agent = ReceptionistAgent()

    def handle_inbound(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        conversation_id = payload["contact_id"]
        tenant_id = payload["tenant_id"]
        business_id = payload["business_id"]
        channel = payload["channel"]
        consent_status = payload.get("consent_status", "granted")
        region = payload.get("metadata", {}).get("region", "US")

        workflow = self.workflow_engine.create_workflow(conversation_id, tenant_id, business_id)

        event = EventEnvelope(
            event_id=f"evt-{datetime.utcnow().timestamp()}",
            tenant_id=tenant_id,
            business_id=business_id,
            conversation_id=conversation_id,
            event_type="inbound.contact.received",
            payload=payload,
            source="api",
            correlation_id=conversation_id,
        )
        self.event_bus.publish(event)

        ok, reason = self.policy_checker.validate(region, channel, consent_status)
        if not ok:
            workflow = self.workflow_engine.update_state(conversation_id, "blocked", {"reason": reason})
            return {
                "success": False,
                "status": "blocked",
                "workflow_state": workflow["state"],
                "reason": reason,
            }

        workflow = self.workflow_engine.update_state(conversation_id, "intent_classified", {
            "intent": "appointment_inquiry",
            "channel": channel,
        })

        agent_result = self.receptionist_agent.run({
            "contact_name": payload.get("contact_name", "there"),
            "intent": "appointment inquiry"
        })

        self.event_bus.publish(EventEnvelope(
            event_id=f"evt-{datetime.utcnow().timestamp()}",
            tenant_id=tenant_id,
            business_id=business_id,
            conversation_id=conversation_id,
            event_type="agent.response.generated",
            payload=agent_result,
            source="receptionist_agent",
            correlation_id=conversation_id,
        ))

        return {
            "success": True,
            "status": "ok",
            "workflow_state": workflow["state"],
            "agent_response": agent_result["response"],
        }
