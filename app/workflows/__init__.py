from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional
import uuid

from app.models import WorkflowState

class WorkflowEngine:
    def __init__(self):
        self.workflows: Dict[str, Dict[str, Any]] = {}

    def create_workflow(self, conversation_id: str, tenant_id: str, business_id: str) -> Dict[str, Any]:
        workflow = {
            "id": str(uuid.uuid4()),
            "conversation_id": conversation_id,
            "tenant_id": tenant_id,
            "business_id": business_id,
            "state": WorkflowState.NEW.value,
            "context": {},
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        self.workflows[conversation_id] = workflow
        return workflow

    def update_state(self, conversation_id: str, new_state: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        workflow = self.workflows[conversation_id]
        workflow["state"] = new_state
        workflow["updated_at"] = datetime.utcnow()

        if context:
            workflow["context"].update(context)

        return workflow

    def get(self, conversation_id: str) -> Dict[str, Any]:
        return self.workflows.get(conversation_id)
