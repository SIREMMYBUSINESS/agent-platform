"""Access to the dialed-number -> tenant business directory."""

from datetime import UTC, datetime

from sqlmodel import Session, select

from app.persistence.models import VoiceNumberRoute
from app.persistence.repositories import _database_call


class SQLVoiceNumberRouteRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, *, e164: str) -> VoiceNumberRoute | None:
        """Return the route for an exact E.164 number regardless of status (provisioning only)."""
        if not isinstance(e164, str) or not e164.strip():
            return None
        return _database_call(lambda: self._session.get(VoiceNumberRoute, e164))

    def get_active(self, *, e164: str) -> VoiceNumberRoute | None:
        """Return the enabled route for an exact E.164 number, or None."""
        if not isinstance(e164, str) or not e164.strip():
            return None
        return _database_call(lambda: self._session.exec(select(VoiceNumberRoute).where(
            VoiceNumberRoute.e164 == e164, VoiceNumberRoute.status == "active",
        )).first())

    def upsert(self, *, e164: str, tenant_id: str, business_id: str,
               status: str = "active") -> VoiceNumberRoute:
        """Provision or update a route. A number maps to exactly one business."""
        row = _database_call(lambda: self._session.get(VoiceNumberRoute, e164))
        if row is None:
            row = VoiceNumberRoute(e164=e164, tenant_id=tenant_id,
                                   business_id=business_id, status=status)
        else:
            row.tenant_id, row.business_id = tenant_id, business_id
            row.status, row.updated_at = status, datetime.now(UTC)
        self._session.add(row)
        self._session.flush()
        return row


__all__ = ["SQLVoiceNumberRouteRepository"]
