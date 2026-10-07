"""Resolve provisioned contact channel, timezone, and language preferences."""


class ContactPreferenceService:
    CHANNELS = {"sms", "email", "whatsapp"}

    def choose_channel(self, *, contact, fallback: str | None = None) -> str | None:
        preferred = (contact.preferred_channel or "").strip().casefold()
        if preferred:
            return preferred if preferred in self.CHANNELS else None
        if fallback in self.CHANNELS and self.destination(contact, fallback):
            return fallback
        for channel in ("sms", "email", "whatsapp"):
            if self.destination(contact, channel):
                return channel
        return None

    @staticmethod
    def destination(contact, channel: str) -> str | None:
        value = (contact.email if channel == "email" else
                 contact.whatsapp if channel == "whatsapp" else
                 contact.phone if channel == "sms" else None)
        return value.strip() if isinstance(value, str) and value.strip() else None

    @staticmethod
    def timezone(*, contact, business) -> str:
        return contact.timezone or business.timezone or "UTC"

    @staticmethod
    def language(*, contact, business) -> str:
        return contact.language or business.default_language or "en"


__all__ = ["ContactPreferenceService"]
