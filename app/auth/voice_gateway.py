"""Service credential for the voice gateway Worker.

The Worker must resolve a dialed number to a tenant *before* any tenant is known, so a
tenant-scoped API key cannot be used. This credential is deliberately separate: it is
accepted only by the number-resolution route, carries no tenant or roles, and only its
SHA-256 digest is held by the platform.
"""

import hashlib
import hmac

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

voice_gateway_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="VoiceGatewayKey",
    description="Voice gateway service key (not a tenant API key)",
)


def require_voice_gateway(
    credential: HTTPAuthorizationCredentials | None = Depends(voice_gateway_bearer),
) -> None:
    if credential is None or not credential.credentials.strip():
        raise HTTPException(401, "Invalid credential", headers={"WWW-Authenticate": "Bearer"})
    expected = settings.voice_gateway_key_sha256
    if not expected:
        raise HTTPException(503, "Voice gateway is not configured")
    supplied = hashlib.sha256(credential.credentials.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(401, "Invalid credential", headers={"WWW-Authenticate": "Bearer"})
