"""High entropy bearer tokens for narrowly scoped contact appointment actions."""
from __future__ import annotations

import hashlib
import secrets


class ContactActionTokens:
    @staticmethod
    def issue() -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        return token, ContactActionTokens.digest(token)

    @staticmethod
    def digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()
