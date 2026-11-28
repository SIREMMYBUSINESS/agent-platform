from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.routes import campaigns, optimization


SECRET_MARKER = "postgres://internal-user:super-secret@db.internal/private"


def _raises(*args, **kwargs):
    raise ValueError(SECRET_MARKER)


def test_campaign_schedule_does_not_return_exception_text(caplog):
    service = SimpleNamespace(schedule_send=_raises)
    request = SimpleNamespace(
        contact_id="contact-1",
        conversation_id=None,
        idempotency_key="key-1",
        scheduled_at=datetime.now(UTC),
        content=SimpleNamespace(model_dump=lambda **_: {"body": "hello"}),
    )

    with pytest.raises(HTTPException) as caught:
        campaigns.schedule_campaign_message(
            uuid4(), request, principal=object(), service=service,
        )

    assert caught.value.status_code == 422
    assert caught.value.detail == (
        "Campaign message could not be scheduled with the supplied details."
    )
    assert SECRET_MARKER not in str(caught.value.detail)
    assert "Campaign message scheduling failed validation" in caplog.text


@pytest.mark.parametrize(
    ("route", "expected_status", "expected_detail", "args"),
    [
        (
            optimization.approve_recommendation, 409,
            "Recommendation could not be approved in its current state.",
            lambda: (uuid4(),),
        ),
        (
            optimization.reject_recommendation, 422,
            "Recommendation could not be rejected with the supplied details.",
            lambda: (uuid4(), SimpleNamespace(rationale="reviewed")),
        ),
    ],
)
def test_recommendation_review_does_not_return_exception_text(
    route, expected_status, expected_detail, args, caplog,
):
    service = SimpleNamespace(approve=_raises, reject=_raises)

    with pytest.raises(HTTPException) as caught:
        route(*args(), principal=object(), service=service)

    assert caught.value.status_code == expected_status
    assert caught.value.detail == expected_detail
    assert SECRET_MARKER not in str(caught.value.detail)
    assert "Optimization recommendation" in caplog.text


def _feedback_request():
    return SimpleNamespace(
        variant=None, action=None, state=None, test_group=None,
        from_channel=None, reason_code=None, sentiment=None,
        sentiment_score=None, response_time_seconds=None,
        resolution_time_seconds=None, customer_satisfaction=None,
        event_key="event-1", correlation_id="corr-1",
        event_type="reply_received", outcome="positive",
        lead_id=None, campaign_id=None, appointment_id=None,
    )


def test_feedback_lookup_does_not_return_exception_text(caplog):
    def fail_lookup(**_):
        raise LookupError(SECRET_MARKER)

    service = SimpleNamespace(capture_manual_feedback=fail_lookup)

    with pytest.raises(HTTPException) as caught:
        optimization.submit_feedback(
            _feedback_request(), service=service, principal=object(),
        )

    assert caught.value.status_code == 404
    assert caught.value.detail == "Feedback target was not found."
    assert SECRET_MARKER not in str(caught.value.detail)
    assert "Optimization feedback target lookup failed" in caplog.text


def test_feedback_validation_does_not_return_exception_text(caplog):
    service = SimpleNamespace(capture_manual_feedback=_raises)

    with pytest.raises(HTTPException) as caught:
        optimization.submit_feedback(
            _feedback_request(), service=service, principal=object(),
        )

    assert caught.value.status_code == 422
    assert caught.value.detail == (
        "Feedback could not be recorded with the supplied details."
    )
    assert SECRET_MARKER not in str(caught.value.detail)
    assert "Optimization feedback validation failed" in caplog.text
