"""Compatibility exports for the canonical persistence models.

New code should import table models from :mod:`app.persistence.models`.
This module remains so existing Version2 imports do not create a second
SQLModel metadata registry.
"""

from app.persistence.models import (
    Appointment,
    AppointmentSlot,
    Business,
    Channel,
    Contact,
    Conversation,
    EventLog,
    EscalationTicket,
    Message,
    RoutingRule,
    Service,
    Staff,
    Tenant,
    WorkflowState,
    WorkflowStateRecord,
)

__all__ = [
    "Appointment",
    "AppointmentSlot",
    "Business",
    "Channel",
    "Contact",
    "Conversation",
    "EventLog",
    "EscalationTicket",
    "Message",
    "RoutingRule",
    "Service",
    "Staff",
    "Tenant",
    "WorkflowState",
    "WorkflowStateRecord",
]
