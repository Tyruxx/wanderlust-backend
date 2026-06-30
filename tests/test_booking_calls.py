from __future__ import annotations

import pytest

from app.core.settings import get_settings
from app.domain.models import DayPlan, Itinerary, ItineraryStatus, PlaceStop, TripBrief
from app.services.booking_calls import BookingCallService, BookingCallStatus, BookingDetails


class FakeBookingCallService(BookingCallService):
    def __init__(self) -> None:
        super().__init__(maps_client=None)
        self.stream_token: str | None = None

    def _create_twilio_call(self, *, to_number: str, stream_token: str) -> str:
        self.stream_token = stream_token
        return "CA1234567890"


@pytest.fixture(autouse=True)
def clear_settings_cache_after_test():
    yield
    get_settings.cache_clear()


def test_twiml_prompts_venue_to_repeat_or_confirm_booking(monkeypatch) -> None:
    _configure_call_env(monkeypatch)
    service = FakeBookingCallService()

    record = service.start_call(
        user_id="user-1",
        itinerary=_itinerary(),
        day_index=0,
        stop_index=0,
        details=_details(),
        confirmed=True,
    )

    assert record.status == BookingCallStatus.QUEUED
    assert service.stream_token is not None

    twiml = service.twiml_for_token(service.stream_token)
    assert "<Connect>" in twiml
    assert "<Stream" in twiml
    assert "<Gather" in twiml
    assert "Press 1 to hear the booking information again" in twiml
    assert "Press 2 if the booking request has been received" in twiml

    repeat_twiml = service.handle_voice_menu_choice(
        stream_token=service.stream_token,
        digits="1",
    )
    assert "tomorrow at 7pm" in repeat_twiml
    assert "<Gather" in repeat_twiml

    booked_twiml = service.handle_voice_menu_choice(
        stream_token=service.stream_token,
        digits="2",
    )
    assert "marked as received" in booked_twiml

    status = service.get_status(record.call_id, user_id="user-1")
    assert status is not None
    assert status.status == BookingCallStatus.BOOKED
    assert status.details is None

    service.update_twilio_status(call_sid="CA1234567890", status="completed")
    still_booked = service.get_status(record.call_id, user_id="user-1")
    assert still_booked is not None
    assert still_booked.status == BookingCallStatus.BOOKED


def test_twilio_completed_without_keypad_confirmation_is_failed(monkeypatch) -> None:
    _configure_call_env(monkeypatch)
    service = FakeBookingCallService()

    record = service.start_call(
        user_id="user-1",
        itinerary=_itinerary(),
        day_index=0,
        stop_index=0,
        details=_details(),
        confirmed=True,
    )

    service.update_twilio_status(call_sid="CA1234567890", status="completed")

    status = service.get_status(record.call_id, user_id="user-1")
    assert status is not None
    assert status.status == BookingCallStatus.FAILED
    assert status.details is None
    assert "before the venue confirmed receipt" in (status.result_summary or "")


def _configure_call_env(monkeypatch) -> None:
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15551230000")
    monkeypatch.setenv("PUBLIC_BACKEND_BASE_URL", "https://wanderlust.example")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-google-key")
    get_settings.cache_clear()


def _details() -> BookingDetails:
    return BookingDetails(
        venue_name="Test Venue",
        venue_phone="+15551234567",
        reservation_datetime="tomorrow at 7pm",
        party_size=2,
        reservation_name="Ada",
        callback_phone="+15550001111",
    )


def _itinerary() -> Itinerary:
    return Itinerary(
        id="itin-1",
        user_id="user-1",
        title="Booking Test",
        status=ItineraryStatus.INACTIVE,
        brief=TripBrief(region="Singapore", description="Booking test", trip_length_days=1),
        preference_version=1,
        days=[
            DayPlan(
                day_number=1,
                start_location="Hotel",
                end_location="Hotel",
                start_time="09:00",
                end_time="18:00",
                stops=[
                    PlaceStop(
                        id="stop-1",
                        name="Test Venue",
                        suggested_order=1,
                        what_to_do="Book a table.",
                    )
                ],
            )
        ],
    )
