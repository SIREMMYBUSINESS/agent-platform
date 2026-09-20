from typing import Dict, Any

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
        return {
            "agent": self.name,
            "response": f"Hi {contact_name}, thanks for contacting us. I can help with {intent}."
        }
