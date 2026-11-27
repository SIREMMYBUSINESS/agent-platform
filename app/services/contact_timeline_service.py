"""Bounded, read-only contact conversation and timeline queries."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.auth.principal_resolver import AuthorizationError
from app.conversation_schemas import ConversationReadResponse
from app.contact_timeline_schemas import (
    ContactConversationsResponse,
    ContactTimelineItemResponse,
    ContactTimelineResponse,
)
from app.persistence.models import EventLog, Message
from app.security import AuthenticatedPrincipal


CONVERSATION_READ_CAP = 100
TIMELINE_PAGE_SIZE_MAX = 100


class ContactTimelineNotFoundError(Exception):
    """The requested contact is not available in the trusted tenant/business scope."""


class ContactTimelineConfigurationError(Exception):
    """The trusted business scope required by contact timeline reads is not configured."""


@dataclass(frozen=True)
class TimelineCursor:
    created_at: datetime
    item_id: UUID
    direction: str


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _sort_key(item: dict[str, Any]) -> tuple[datetime, str]:
    return _utc(item["created_at"]), str(item["id"])


def merge_timeline_items(
    messages: Sequence[Message], events: Sequence[EventLog],
) -> list[dict[str, Any]]:
    """Purely merge message and event rows into one ascending (created_at, id) stream."""
    merged: list[dict[str, Any]] = []
    for message in messages:
        merged.append({
            "id": str(message.id),
            "kind": "message",
            "conversation_id": message.conversation_id,
            "created_at": _utc(message.created_at),
            "channel": message.channel,
            "direction": message.direction,
            "sender": message.sender,
            "subject": message.subject,
            "content": message.content,
            "delivery_status": message.delivery_status,
        })
    for event in events:
        merged.append({
            "id": str(event.id),
            "kind": "event",
            "conversation_id": event.conversation_id,
            "created_at": _utc(event.created_at),
            "event_type": event.event_type,
            "payload": dict(event.payload or {}),
            "source": event.source,
        })
    return sorted(merged, key=_sort_key)


def encode_timeline_cursor(created_at: datetime, item_id: UUID, direction: str) -> str:
    if direction not in {"older", "newer"}:
        raise ValueError("Invalid cursor direction")
    payload = {
        "v": 1,
        "created_at": _utc(created_at).isoformat(),
        "id": str(item_id),
        "direction": direction,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_timeline_cursor(token: str) -> TimelineCursor:
    if not token or len(token) > 2048:
        raise ValueError("Invalid timeline cursor")
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("v") != 1:
            raise ValueError("Unsupported cursor version")
        created_at = datetime.fromisoformat(payload["created_at"])
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise ValueError("Cursor timestamp must include a timezone")
        item_id = UUID(payload["id"])
        direction = payload["direction"]
        if direction not in {"older", "newer"}:
            raise ValueError("Invalid cursor direction")
        return TimelineCursor(_utc(created_at), item_id, direction)
    except (binascii.Error, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError("Invalid timeline cursor") from exc


class ContactTimelineService:
    def __init__(
        self, *, uow_factory, trusted_business_id: str | None,
        conversation_cap: int = CONVERSATION_READ_CAP,
    ) -> None:
        if not 1 <= conversation_cap <= CONVERSATION_READ_CAP:
            raise ValueError("conversation_cap must be between 1 and 100")
        self._uow_factory = uow_factory
        self._business_id = trusted_business_id.strip() if trusted_business_id else None
        self._conversation_cap = conversation_cap

    @staticmethod
    def _authorize(principal: AuthenticatedPrincipal) -> str:
        if not isinstance(principal, AuthenticatedPrincipal):
            raise AuthorizationError("Contact timeline access denied")
        now = datetime.now(UTC)
        if (
            "conversation_reader" not in principal.roles
            or principal.issued_at > now
            or (principal.expires_at is not None and principal.expires_at <= now)
        ):
            raise AuthorizationError("Contact timeline access denied")
        return principal.tenant_id

    def _business_scope(self) -> str:
        if not self._business_id:
            raise ContactTimelineConfigurationError(
                "LEAD_BUSINESS_ID must be configured for contact conversation reads"
            )
        return self._business_id

    def _load_contact_conversations(self, uow, *, tenant_id: str, contact_id: str):
        business_id = self._business_scope()
        contact = uow.contacts.get(tenant_id=tenant_id, contact_id=contact_id)
        if contact is None or contact.business_id != business_id:
            raise ContactTimelineNotFoundError("Contact not found")
        fetched = uow.conversations.list_for_contact_in_business(
            tenant_id=tenant_id,
            business_id=business_id,
            contact_id=contact_id,
            limit=self._conversation_cap + 1,
        )
        truncated = len(fetched) > self._conversation_cap
        return fetched[:self._conversation_cap], truncated

    @staticmethod
    def _conversation_response(uow, row) -> ConversationReadResponse:
        assignment = uow.conversation_assignments.get(
            tenant_id=row.tenant_id, conversation_id=row.id,
        )
        return ConversationReadResponse(
            id=row.id,
            tenant_id=row.tenant_id,
            business_id=row.business_id,
            contact_id=row.contact_id,
            channel=row.channel,
            status=row.status,
            version=row.version,
            workflow_state=row.workflow_state,
            assigned_staff_id=assignment.staff_id if assignment else None,
            started_at=row.started_at,
            updated_at=row.updated_at,
        )

    def list_conversations(
        self, principal: AuthenticatedPrincipal, *, contact_id: str,
    ) -> ContactConversationsResponse:
        tenant_id = self._authorize(principal)
        with self._uow_factory() as uow:
            conversations, truncated = self._load_contact_conversations(
                uow, tenant_id=tenant_id, contact_id=contact_id,
            )
            items = [self._conversation_response(uow, row) for row in conversations]
        return ContactConversationsResponse(
            items=items,
            conversations_included=len(items),
            conversation_limit=self._conversation_cap,
            conversations_truncated=truncated,
        )

    def timeline(
        self, principal: AuthenticatedPrincipal, *, contact_id: str,
        limit: int = 50, cursor: str | None = None,
    ) -> ContactTimelineResponse:
        tenant_id = self._authorize(principal)
        if not 1 <= limit <= TIMELINE_PAGE_SIZE_MAX:
            raise ValueError("limit must be between 1 and 100")
        decoded = decode_timeline_cursor(cursor) if cursor is not None else None
        direction = decoded.direction if decoded else "older"
        cursor_created_at = decoded.created_at if decoded else None
        cursor_id = decoded.item_id if decoded else None

        with self._uow_factory() as uow:
            conversations, truncated = self._load_contact_conversations(
                uow, tenant_id=tenant_id, contact_id=contact_id,
            )
            conversation_ids = [row.id for row in conversations]
            fetch_limit = limit + 1
            messages = uow.messages.list_for_conversations_page(
                tenant_id=tenant_id,
                conversation_ids=conversation_ids,
                cursor_created_at=cursor_created_at,
                cursor_id=cursor_id,
                direction=direction,
                limit=fetch_limit,
            )
            events = uow.events.list_for_conversations_page(
                tenant_id=tenant_id,
                conversation_ids=conversation_ids,
                cursor_created_at=cursor_created_at,
                cursor_id=cursor_id,
                direction=direction,
                limit=fetch_limit,
            )
            # Materialize ORM rows before the UnitOfWork closes its session.
            merged = merge_timeline_items(messages, events)

        ordered = sorted(merged, key=_sort_key, reverse=(direction == "older"))
        has_more = len(ordered) > limit
        selected = ordered[:limit]
        if direction == "older":
            selected.sort(key=_sort_key)
        items = [ContactTimelineItemResponse(**item) for item in selected]

        older_cursor = None
        newer_cursor = None
        if items:
            if direction == "older":
                if has_more:
                    older_cursor = encode_timeline_cursor(items[0].created_at, UUID(items[0].id), "older")
                if decoded is not None:
                    newer_cursor = encode_timeline_cursor(items[-1].created_at, UUID(items[-1].id), "newer")
            else:
                older_cursor = encode_timeline_cursor(items[0].created_at, UUID(items[0].id), "older")
                if has_more:
                    newer_cursor = encode_timeline_cursor(items[-1].created_at, UUID(items[-1].id), "newer")

        return ContactTimelineResponse(
            items=items,
            limit=limit,
            older_cursor=older_cursor,
            newer_cursor=newer_cursor,
            conversations_included=len(conversations),
            conversation_limit=self._conversation_cap,
            conversations_truncated=truncated,
        )


__all__ = [
    "CONVERSATION_READ_CAP",
    "ContactTimelineConfigurationError",
    "ContactTimelineNotFoundError",
    "ContactTimelineService",
    "decode_timeline_cursor",
    "encode_timeline_cursor",
    "merge_timeline_items",
]
