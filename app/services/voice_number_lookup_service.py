"""Database-backed exact voice number routing."""
from __future__ import annotations
import re
from sqlmodel import Session, select
from app.persistence.models import VoiceNumberRoute
_E164 = re.compile(r"^\+[1-9][0-9]{6,14}$")
class VoiceNumberLookupService:
    def __init__(self, session: Session): self.session = session
    def resolve(self, *, dialed_number: str) -> dict[str, str] | None:
        if not isinstance(dialed_number, str) or not _E164.fullmatch(dialed_number):
            raise ValueError("number must be E.164")
        row = self.session.exec(select(VoiceNumberRoute).where(
            VoiceNumberRoute.e164 == dialed_number, VoiceNumberRoute.status == "active"  # noqa: E712
        )).first()
        return None if row is None else {"tenant_id": row.tenant_id, "business_id": row.business_id}
