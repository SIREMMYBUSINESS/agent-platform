"""Platform-owned SendGrid sender for passwordless workspace login links."""

from __future__ import annotations

import html
import json
from typing import Protocol

from app.integrations.provider_support import ProviderHTTPError, UrllibJSONTransport


class MagicLinkMailer(Protocol):
    def send_magic_link(self, *, recipient: str, link: str) -> None: ...


class SendGridMagicLinkMailer:
    """Use a platform credential, not a tenant's optional outreach credentials."""

    endpoint = "https://api.sendgrid.com/v3/mail/send"

    def __init__(
        self, *, api_key: str, from_email: str,
        transport=None, timeout: float = 10.0,
    ) -> None:
        if not api_key.strip() or not from_email.strip() or timeout <= 0:
            raise ValueError("magic-link email configuration is invalid")
        self._api_key = api_key
        self._from_email = from_email
        self._transport = transport or UrllibJSONTransport()
        self._timeout = timeout

    def send_magic_link(self, *, recipient: str, link: str) -> None:
        payload = {
            "personalizations": [{"to": [{"email": recipient}]}],
            "from": {"email": self._from_email},
            "subject": "Your Agent Platform sign-in link",
            "content": [
                {
                    "type": "text/plain",
                    "value": (
                        "Use this one-time link to sign in to Agent Platform. "
                        "It expires shortly and can only be used once.\n\n"
                        f"{link}\n\nIf you did not request this link, you can ignore this email."
                    ),
                },
                {
                    "type": "text/html",
                    "value": (
                        "<p>Use this one-time link to sign in to Agent Platform.</p>"
                        f'<p><a href="{html.escape(link, quote=True)}">Sign in to Agent Platform</a></p>'
                        "<p>This link expires shortly and can only be used once. "
                        "If you did not request it, you can ignore this email.</p>"
                    ),
                },
            ],
        }
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        try:
            status, _response = self._transport.request(
                "POST", self.endpoint,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                body=body,
                timeout=self._timeout,
            )
        except ProviderHTTPError:
            raise RuntimeError("magic_link_email_delivery_failed") from None
        if status != 202:
            raise RuntimeError("magic_link_email_delivery_failed")


__all__ = ["MagicLinkMailer", "SendGridMagicLinkMailer"]
