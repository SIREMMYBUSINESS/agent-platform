from app.agents import ReceptionistAgent
from app.workflows import WorkflowEngine
from app.policies import PolicyChecker

class TaskDispatcher:
    def __init__(self):
        self.workflow_engine = WorkflowEngine()
        self.policy_checker = PolicyChecker()
        self.receptionist = ReceptionistAgent()

    def handle_inbound(self, payload: dict):
        conversation_id = payload["contact_id"]
        workflow = self.workflow_engine.create_workflow(conversation_id, payload["tenant_id"])
        ok, msg = self.policy_checker.validate(
            payload["tenant_id"],
            payload["channel"],
            "US",
            "granted"
        )
        if not ok:
            workflow["state"] = "blocked"
            return {"status": "blocked", "reason": msg}

        workflow = self.workflow_engine.update_state(conversation_id, "intent_classified", {
            "intent": "appointment_inquiry"
        })

        agent_result = self.receptionist.run({
            "contact_name": "Alex",
            "intent": "appointment inquiry"
        })

        return {
            "status": "ok",
            "workflow": workflow,
            "agent_result": agent_result
        }
