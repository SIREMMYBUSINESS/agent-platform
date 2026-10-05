"""Email provider adapters."""

from app.integrations.email.smtp import SMTPEmailSender

__all__ = ["SMTPEmailSender"]
