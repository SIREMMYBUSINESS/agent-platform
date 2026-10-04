"""Read and append explicit channel-scoped consent lifecycle records."""
from __future__ import annotations

from datetime import UTC, datetime


class ConsentService:
    STATUSES = {"granted", "revoked", "expired", "unknown"}

    def status(self, uow, *, tenant_id: str, business_id: str,
               contact, channel: str, now: datetime | None = None) -> str:
        now = now or datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        record = uow.consent_records.current(
            tenant_id=tenant_id, business_id=business_id,
            contact_id=contact.id, channel=channel,
        )
        if record is not None:
            if record.status == "granted" and record.expires_at is not None:
                expires_at = record.expires_at
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=UTC)
                if expires_at <= now:
                    return "expired"
            return record.status

        # Phase 2C stored opt-outs separately. Honor those durable revocations
        # while the canonical append-only ConsentRecord table is introduced.
        legacy = uow.contact_channel_consents.get(
            tenant_id=tenant_id, business_id=business_id,
            contact_id=contact.id, channel=channel,
        )
        if legacy is not None and legacy.status == "revoked":
            return "revoked"
        legacy_status = getattr(contact, "consent_status", "unknown")
        if legacy_status in {"denied", "revoked"}:
            return "revoked"
        if legacy_status == "expired":
            return "expired"
        # The old contact-level "granted" flag has no channel, source, or
        # expiry evidence. It cannot satisfy a channel-specific consent gate.
        return "unknown"

    def record(self, uow, *, tenant_id: str, business_id: str,
               contact_id: str, channel: str, status: str,
               source: str, source_reference: str | None = None,
               expires_at: datetime | None = None):
        if status not in self.STATUSES:
            raise ValueError(f"unsupported consent status: {status}")
        return uow.consent_records.add(
            tenant_id=tenant_id, business_id=business_id,
            contact_id=contact_id, channel=channel, status=status,
            source=source, source_reference=source_reference,
            expires_at=expires_at,
        )

    def revoke(self, uow, *, tenant_id: str, business_id: str,
               contact_id: str, channel: str, source: str = "opt_out"):
        row = self.record(uow, tenant_id=tenant_id, business_id=business_id,
                          contact_id=contact_id, channel=channel,
                          status="revoked", source=source)
        # Keep the existing Phase 2C fast-path state synchronized for older
        # readers while ConsentRecord becomes the canonical lifecycle history.
        uow.contact_channel_consents.revoke(
            tenant_id=tenant_id, business_id=business_id,
            contact_id=contact_id, channel=channel,
        )
        return row


__all__ = ["ConsentService"]
