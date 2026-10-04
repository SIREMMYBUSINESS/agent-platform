"""High-entropy API-key generation and verification behind a repository port."""

import hashlib
import hmac
import re
import secrets
from typing import Protocol

from app.auth.principal_resolver import VerifiedCredential


def generate_api_key() -> str:
    """Return a 256-bit secret for one-time delivery via trusted provisioning."""
    return "ap_" + secrets.token_urlsafe(32)


def hash_api_key(key: str) -> str:
    """Hash generated random keys; this is not a password hashing function."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class ApiKeyRepository(Protocol):
    def find_by_hash(self, key_hash: str) -> tuple[str, VerifiedCredential] | None: ...


class ApiKeyVerifier:
    def __init__(self, repository: ApiKeyRepository) -> None:
        self._repository = repository

    def lookup_verified(self, credential: str) -> VerifiedCredential | None:
        if not isinstance(credential, str) or re.fullmatch(r"ap_[A-Za-z0-9_-]{43}", credential) is None:
            return None
        digest = hash_api_key(credential)
        record = self._repository.find_by_hash(digest)
        if record is None:
            return None
        stored_hash, identity = record
        return identity if hmac.compare_digest(stored_hash, digest) else None
