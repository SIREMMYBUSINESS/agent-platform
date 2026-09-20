from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel, Relationship

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
    BLOCKED = "blocked"

class Tenant(SQLModel, table=True):
    id: Optional[str] = Field(default=None, primary_key=True)
    name: str
    region: str = "US"
    default_timezone: str = "UTC"
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Business(SQLModel, table=True):
    id: Optional[str] = Field(default=None, primary_key=True)
    tenant_id: str = Field(foreign_key="tenant.id")
    name: str
    vertical: str
    region: str = "US"
    default_language: str = "en"
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Contact(SQLModel, table=True):
    id: Optional[str] = Field(default=None, primary_key=True)
    tenant_id: str = Field(foreign_key="tenant.id")
    business_id: str = Field(foreign_key="business.id")
    full_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    whatsapp: Optional[str] = None
    consent_status: str = "unknown"
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Conversation(SQLModel, table=True):
    id: Optional[str] = Field(default=None, primary_key=True)
    tenant_id: str = Field(foreign_key="tenant.id")
    business_id: str = Field(foreign_key="business.id")
    contact_id: str = Field(foreign_key="contact.id")
    channel: str
    workflow_state: str = WorkflowState.NEW.value
    correlation_id: Optional[str] = None
    started_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class Message(SQLModel, table=True):
    id: Optional[str] = Field(default=None, primary_key=True)
    conversation_id: str = Field(foreign_key="conversation.id")
    sender: str
    channel: str
    content: str
    direction: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Appointment(SQLModel, table=True):
    id: Optional[str] = Field(default=None, primary_key=True)
    conversation_id: str = Field(foreign_key="conversation.id")
    lead_id: Optional[str] = None
    staff_id: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    status: str = "proposed"
    created_at: datetime = Field(default_factory=datetime.utcnow)

class WorkflowStateRecord(SQLModel, table=True):
    id: Optional[str] = Field(default=None, primary_key=True)
    conversation_id: str = Field(foreign_key="conversation.id")
    state: str
    context_json: str = "{}"
    created_at: datetime = Field(default_factory=datetime.utcnow)

class EventLog(SQLModel, table=True):
    id: Optional[str] = Field(default=None, primary_key=True)
    tenant_id: str = Field(foreign_key="tenant.id")
    business_id: str = Field(foreign_key="business.id")
    conversation_id: str = Field(foreign_key="conversation.id")
    event_type: str
    payload_json: str
    source: str
    correlation_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)