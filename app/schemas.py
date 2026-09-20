from typing import Optional, Dict, Any
from pydantic import BaseModel

class InboundRequest(BaseModel):
    tenant_id: str
    business_id: str
    contact_id: str
    channel: str
    text: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = {}
