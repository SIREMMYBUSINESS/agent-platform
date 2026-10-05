"""Application-facing CRM synchronization operations."""

from __future__ import annotations

from uuid import UUID

from app.integrations.crm.events import CRMEvent, CRMEventType
from app.integrations.crm.sync_engine import CRMSyncEngine
from app.security import AuthenticatedPrincipal


class CRMIntegrationService:
    def __init__(self, engine: CRMSyncEngine) -> None:
        self.engine = engine

    def sync_contact(self, principal: AuthenticatedPrincipal, contact_id: str, *,
                     idempotency_key: str, event_type: str = CRMEventType.CONTACT_UPDATED):
        return self.engine.sync_outbound(principal, CRMEvent(
            "contact", contact_id, event_type, idempotency_key,
        ))

    def sync_conversation(self, principal: AuthenticatedPrincipal, conversation_id: UUID, *,
                          idempotency_key: str, event_type: str,
                          payload: dict | None = None):
        return self.engine.sync_outbound(principal, CRMEvent(
            "conversation", str(conversation_id), event_type, idempotency_key, payload,
        ))

    def sync_campaign_event(self, principal: AuthenticatedPrincipal, campaign_id: UUID, *,
                            idempotency_key: str, event_type: str,
                            payload: dict | None = None):
        return self.engine.sync_outbound(principal, CRMEvent(
            "campaign", str(campaign_id), event_type, idempotency_key, payload,
        ))

    def sync_inbound_contact(self, principal: AuthenticatedPrincipal, payload: dict, *,
                             idempotency_key: str):
        return self.engine.sync_inbound(principal, payload=payload,
                                        idempotency_key=idempotency_key)

    def retry(self, principal: AuthenticatedPrincipal, record_id: UUID):
        return self.engine.retry_sync(principal, record_id)


__all__ = ["CRMIntegrationService"]
