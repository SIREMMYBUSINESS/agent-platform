from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime

class Channel(str, Enum):
    PHONE = "phone"
    CHAT = "chat"
    SMS = "sms"
    WHATSAPP = "whatsapp"
    EMAIL = "email"
    VOICEMAIL = "voicemail"

class WorkflowState(str, Enum):
    NEW = "new"
    INTENT_CLASSIFIED = "intent_classified"
    QUALIFYING = "qualifying"
    SCHEDULE_REQUESTED = "schedule_requested"
    APPOINTMENT_CONFIRMED = "appointment_confirmed"
    FOLLOW_UP_SENT = "follow_up_sent"
    ESCALATED = "escalated"
    RESOLVED = "resolved"
    CLOSED = "closed"

class Contact(BaseModel):
    id: str
    tenant_id: str
    business_id: str
    full_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    whatsapp: Optional[str] = None
    consent_status: str = "unknown"

class Conversation(BaseModel):
    id: str
    tenant_id: str
    business_id: str
    contact_id: str
    channel: Channel
    workflow_state: WorkflowState = WorkflowState.NEW
    correlation_id: Optional[str] = None
    started_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class Message(BaseModel):
    id: str
    conversation_id: str
    sender: str  # "user" or "agent"
    channel: Channel
    content: str
    direction: str  # "inbound" or "outbound"
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Appointment(BaseModel):
    id: str
    conversation_id: str
    lead_id: Optional[str] = None
    staff_id: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    status: str = "proposed"
