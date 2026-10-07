from types import SimpleNamespace

from app.services.contact_preference_service import ContactPreferenceService


def test_explicit_unavailable_preference_does_not_fall_back_to_another_channel():
    contact = SimpleNamespace(preferred_channel="email", email=None, phone="+15555550123", whatsapp=None)
    assert ContactPreferenceService().choose_channel(contact=contact, fallback="sms") == "email"
    assert ContactPreferenceService().destination(contact, "email") is None


def test_unset_preference_uses_available_inbound_channel_then_contact_address():
    preferences = ContactPreferenceService()
    contact = SimpleNamespace(preferred_channel=None, email="person@example.test", phone=None, whatsapp=None)
    assert preferences.choose_channel(contact=contact, fallback="sms") == "email"
    assert preferences.language(
        contact=SimpleNamespace(language=None), business=SimpleNamespace(default_language="fr"),
    ) == "fr"
