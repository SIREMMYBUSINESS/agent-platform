from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

class Channel(str, Enum):
    PHONE = "phone"
    CHAT = "chat"
    SMS = "sms"
    WHATSAPP = "whatsapp"
    EMAIL = "email"
    VOICEMAIL = "voicemail"

class WorkflowState(str, Enum):
    NEW = "new"
    INTAKE_RECEIVED = "intake_received"
    INTENT_CLASSIFIED = "intent_classified"
    BUSINESS_HOURS_CHECKED = "business_hours_checked"
    APPOINTMENT_PROPOSED = "appointment_proposed"
    APPOINTMENT_CONFIRMED = "appointment_confirmed"
    REMINDER_PREPARED = "reminder_prepared"
    QUALIFYING = "qualifying"
    SCHEDULE_REQUESTED = "schedule_requested"
    FOLLOW_UP_SENT = "follow_up_sent"
    ESCALATED = "escalated"
    RESOLVED = "resolved"
    CLOSED = "closed"
    BLOCKED = "blocked"
    AFTER_HOURS_VOICEMAIL = "after_hours_voicemail"
    CALLBACK_REQUESTED = "callback_requested"


class LeadLifecycle(str, Enum):
    IMPORTED = "imported"
    ENRICHED = "enriched"
    SCORED = "scored"
    QUALIFIED = "qualified"
    CONTACTED = "contacted"
    RESPONDED = "responded"
    BOOKED = "booked"
    CONVERTED = "converted"
    OPTED_OUT = "opted_out"
    CLOSED = "closed"


class CampaignStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELED = "canceled"

class ContactCreate(BaseModel):
    tenant_id: str
    business_id: str
    full_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    whatsapp: Optional[str] = None
    consent_status: str = "unknown"

class ConversationCreate(BaseModel):
    tenant_id: str
    business_id: str
    contact_id: str
    channel: Channel
    correlation_id: Optional[str] = None

class MessageCreate(BaseModel):
    conversation_id: str
    sender: str
    channel: Channel
    content: str
    direction: str

class InboundRequest(BaseModel):
    tenant_id: str
    business_id: str
    contact_id: str
    channel: str
    text: Optional[str] = None
    contact_name: Optional[str] = None
    consent_status: str = "granted"
    metadata: dict = Field(default_factory=dict)
