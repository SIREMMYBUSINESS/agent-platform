from app.services.receptionist_intake_service import ReceptionistIntakeService


def test_receptionist_classifies_urgent_before_appointment_and_extracts_entities():
    result = ReceptionistIntakeService().classify(
        "I need an appointment for consultation Monday morning because of back pain",
        service_names=("Consultation", "Follow-up"),
    )
    assert result.intent == "appointment"
    assert result.priority == "routine"
    assert result.entities == {
        "weekday_preference": 0,
        "time_preference": ["06:00:00", "12:00:00"],
        "service_type": "Consultation",
        "reason": "back pain",
    }


def test_receptionist_urgent_and_confirmation_intents():
    service = ReceptionistIntakeService()
    urgent = service.classify("I have severe pain and need to book now")
    confirmation = service.classify("Option 2")
    assert (urgent.intent, urgent.priority) == ("urgent", "urgent")
    assert confirmation.intent == "appointment_confirmation"


def test_receptionist_ignores_invalid_calendar_date():
    result = ReceptionistIntakeService().classify("Book an appointment on 2026-19-42")
    assert "date_preference" not in result.entities
