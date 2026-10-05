"""Tenant-configured SMTP adapter implementing the outreach sender contract."""

from __future__ import annotations

import hashlib
import smtplib
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Mapping

from app.integrations.provider_support import (
    ProviderAuditSink, ProviderConfigurationError, TenantConfigResolver, emit_audit,
)


class EmailDeliveryError(RuntimeError):
    """Sanitized SMTP delivery failure."""


class SMTPEmailSender:
    provider_key = "smtp"

    def __init__(self, *, configs: TenantConfigResolver, audit_sink: ProviderAuditSink,
                 timeout: float = 10.0,
                 smtp_factory=smtplib.SMTP) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._configs = configs
        self._audit = audit_sink
        self._timeout = timeout
        self._smtp_factory = smtp_factory

    def send(self, *, tenant_id: str, business_id: str, channel: str,
             recipient: str, content: Mapping[str, str], idempotency_key: str) -> None:
        if channel != "email":
            raise ValueError("SMTPEmailSender only supports email")
        key = self._key(idempotency_key)
        try:
            config = self._configs.resolve(tenant_id=tenant_id, business_id=business_id)
            host = self._required(config, "host")
            sender = self._required(config, "from_address")
            self._validate_email(recipient)
            self._validate_email(sender)
            subject = content.get("subject", "")
            body = content.get("body", "")
            if not body.strip():
                raise ValueError("email body is required")
            message = EmailMessage()
            message["To"] = recipient
            message["From"] = sender
            message["Subject"] = subject or "Message from your business"
            message["Message-ID"] = self._message_id(key, sender)
            message["X-Idempotency-Key"] = key
            message.set_content(body)
            port = int(config.get("port", "465" if config.get("implicit_tls") == "true" else "587"))
            implicit_tls = config.get("implicit_tls", "false").lower() == "true"
            smtp_type = smtplib.SMTP_SSL if implicit_tls else self._smtp_factory
            with smtp_type(host, port, timeout=self._timeout) as client:
                if not implicit_tls and config.get("starttls", "true").lower() == "true":
                    client.starttls()
                username = config.get("username")
                password = config.get("password")
                if username or password:
                    if not username or not password:
                        raise ProviderConfigurationError("SMTP username and password must be configured together")
                    client.login(username, password)
                client.send_message(message)
            emit_audit(self._audit, tenant_id=tenant_id, business_id=business_id,
                       provider=self.provider_key, operation="send_email",
                       idempotency_key=key, outcome="succeeded")
        except Exception as exc:
            code = (f"smtp_error:{type(exc).__name__}" if not isinstance(exc, ValueError)
                    else "invalid_email_request")
            self._emit_failure(tenant_id, business_id, key, code)
            if isinstance(exc, ValueError):
                raise
            raise EmailDeliveryError(code) from None

    def _emit_failure(self, tenant_id, business_id, key, code):
        emit_audit(self._audit, tenant_id=tenant_id, business_id=business_id,
                   provider=self.provider_key, operation="send_email",
                   idempotency_key=key, outcome="failed", error_code=code)

    @staticmethod
    def _required(config, key):
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ProviderConfigurationError(f"SMTP {key} is not configured")
        return value.strip()

    @staticmethod
    def _validate_email(value):
        if not isinstance(value, str) or parseaddr(value)[1] != value or "@" not in value:
            raise ValueError("valid email address is required")

    @staticmethod
    def _message_id(key, sender):
        digest = hashlib.sha256(key.encode()).hexdigest()
        domain = sender.rsplit("@", 1)[-1].strip(">")
        return f"<{digest}@{domain}>"

    @staticmethod
    def _key(value):
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise ValueError("idempotency_key must be between 1 and 200 characters")
        return value.strip()


__all__ = ["EmailDeliveryError", "SMTPEmailSender"]
