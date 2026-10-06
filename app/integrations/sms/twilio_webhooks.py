"""Signature validation and URL construction for Twilio status callbacks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from uuid import UUID
from twilio.request_validator import RequestValidator


class _TwilioParameterMap(dict[str, list[str]]):
    """Twilio SDK-compatible multivalue form map (``Request``/``QueryDict`` shape)."""

    def getlist(self, key: str) -> list[str]:
        return list(self[key])


def build_status_callback_url(base_url: str, *, tenant_id: str, business_id: str,
                              message_id: UUID, attempt: int) -> str:
    parsed = urlsplit(base_url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError("Twilio status callback base URL must be HTTPS without query or fragment")
    path = parsed.path.rstrip("/") + "/" + quote(tenant_id, safe="") + "/" + quote(business_id, safe="")
    if attempt < 1:
        raise ValueError("delivery attempt must be positive")
    return urlunsplit((parsed.scheme, parsed.netloc, path,
                       urlencode({"message_id": str(message_id), "attempt": attempt}), ""))


def validate_twilio_signature(*, callback_url: str,
                              parameters: Mapping[str, str | Sequence[str]],
                              signature: str, auth_token: str) -> bool:
    """Validate Twilio's form callback signature, accepting evolving fields.

    The callback URL must come from trusted tenant configuration, not Host or
    forwarded headers. Repeated form values are sorted to match Twilio SDK rules.
    """
    if not signature or not auth_token:
        return False
    normalized = _TwilioParameterMap({
        key: list(value) if isinstance(value, (list, tuple)) else [value]
        for key, value in parameters.items()
    })
    return RequestValidator(auth_token).validate(callback_url, normalized, signature)
