from __future__ import annotations

from typing import Any, Dict

class BaseAgent:
    def __init__(self, name: str):
        self.name = name

    def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

class ReceptionistAgent(BaseAgent):
    def __init__(self):
        super().__init__("receptionist")

    def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        contact_name = context.get("contact_name", "there")
        intent = context.get("intent", "general inquiry")

        response = (
            f"Hi {contact_name}, thanks for reaching out. "
            f"I can help with your {intent}."
        )

        return {
            "agent": self.name,
            "response": response,
            "needs_human": False,
            "intent": intent,
        }
