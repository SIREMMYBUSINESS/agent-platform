from enum import Enum
from typing import Dict, Any
from datetime import datetime
import uuid

class WorkflowEngine:
    def __init__(self):
        self.workflows: Dict[str, Dict[str, Any]] = {}

    def create_workflow(self, conversation_id: str, tenant_id: str):
        wf = {
            "id": str(uuid.uuid4()),
            "conversation_id": conversation_id,
            "tenant_id": tenant_id,
            "state": "new",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            "context": {}
        }
        self.workflows[conversation_id] = wf
        return wf

    def update_state(self, conversation_id: str, new_state: str, context: Dict[str, Any] | None = None):
        wf = self.workflows[conversation_id]
        wf["state"] = new_state
        wf["updated_at"] = datetime.utcnow()
        if context:
            wf["context"].update(context)
        return wf
