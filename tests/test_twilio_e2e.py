from __future__ import annotations

import os
from pathlib import Path
from time import sleep

import httpx
import pytest

from app.core.settings import get_settings
from app.domain.models import DayPlan, Itinerary, ItineraryStatus, PlaceStop, TripBrief
from app.services.booking_calls import BookingCallService, BookingCallStatus, BookingDetails


def test_twilio_booking_call_e2e_env_and_outbound_call() -> None:
    """Opt-in smoke test for the real Twilio booking-call path.

    This places a real outbound call and may incur Twilio charges. It is skipped
    unless WANDERLUST_RUN_TWILIO_E2E=1 is present in the shell for this run.
    """

    if os.environ.get("WANDERLUST_RUN_TWILIO_E2E") != "1":
        pytest.skip("Set WANDERLUST_RUN_TWILIO_E2E=1 to place a real Twilio test call.")

    get_settings.cache_clear()
    settings = get_settings()
    to_number = _env_value("WANDERLUST_TWILIO_E2E_TO_NUMBER")
    callback_phone = _env_value("WANDERLUST_TWILIO_E2E_CALLBACK_PHONE") or to_number
    missing = [
        name
        for name, value in {
            "TWILIO_ACCOUNT_SID": settings.twilio_account_sid,
            "TWILIO_AUTH_TOKEN": settings.twilio_auth_token,
            "TWILIO_FROM_NUMBER": settings.twilio_from_number,
            "PUBLIC_BACKEND_BASE_URL": settings.public_backend_base_url,
            "GOOGLE_API_KEY": settings.google_api_key,
            "WANDERLUST_TWILIO_E2E_TO_NUMBER": to_number,
        }.items()
        if not value
    ]
    assert not missing, "Missing required Twilio e2e env values: " + ", ".join(missing)
    assert settings.public_backend_base_url.startswith(
        "https://"
    ), "PUBLIC_BACKEND_BASE_URL must be a public HTTPS URL for Twilio voice webhooks."

    public_base = settings.public_backend_base_url.rstrip("/")
    ready = httpx.get(f"{public_base}/readyz", timeout=10)
    assert ready.status_code == 200

    probe_token = "wanderlust-e2e-probe-token"
    twiml = httpx.get(f"{public_base}/v1/booking-calls/twiml/{probe_token}", timeout=10)
    assert twiml.status_code == 200
    assert "<Stream" in twiml.text
    assert "wss://" in twiml.text
    assert probe_token in twiml.text

    service = BookingCallService(maps_client=_NoopMapsClient())  # type: ignore[arg-type]
    details = BookingDetails(
        venue_name="Wanderlust Twilio E2E Venue",
        venue_phone=to_number,
        reservation_datetime="tomorrow at 7pm",
        party_size=2,
        reservation_name="Wanderlust Test",
        callback_phone=callback_phone or to_number,
        special_requests="End-to-end Twilio smoke test; no booking should be made.",
    )

    twilio_sid: str | None = None
    try:
        record = service.start_call(
            user_id="twilio-e2e-user",
            itinerary=_e2e_itinerary(),
            day_index=0,
            stop_index=0,
            details=details,
            confirmed=True,
        )
        twilio_sid = record.twilio_call_sid
        assert record.status == BookingCallStatus.QUEUED
        assert twilio_sid is not None and twilio_sid.startswith("CA")
        assert record.details is None
        assert record.fallback_instructions

        # Allow Twilio to register the call, then simulate the terminal callback
        # locally to verify the app's status scrubbing guardrail.
        sleep(1)
        service.update_twilio_status(call_sid=twilio_sid, status="completed")
        status = service.get_status(record.call_id, user_id="twilio-e2e-user")
        assert status is not None
        assert status.status == BookingCallStatus.COMPLETED
        assert status.details is None
    finally:
        if twilio_sid:
            _hang_up_twilio_call(twilio_sid)


class _NoopMapsClient:
    def find_phone_number(self, query: str) -> str | None:
        return None


def _e2e_itinerary() -> Itinerary:
    return Itinerary(
        id="twilio-e2e-itinerary",
        user_id="twilio-e2e-user",
        title="Twilio E2E",
        status=ItineraryStatus.INACTIVE,
        brief=TripBrief(region="E2E", description="Twilio booking-call smoke test", trip_length_days=1),
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
                        id="twilio-e2e-stop",
                        name="Wanderlust Twilio E2E Venue",
                        suggested_order=1,
                        what_to_do="Run the Twilio booking-call smoke test.",
                    )
                ],
            )
        ],
    )


def _hang_up_twilio_call(call_sid: str) -> None:
    settings = get_settings()
    from twilio.rest import Client

    client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    try:
        client.calls(call_sid).update(status="completed")
    except Exception:
        # The call may have already completed, failed, or been rejected by a trial account.
        pass


def _env_value(name: str) -> str:
    if value := os.environ.get(name):
        return value.strip()
    env_path = Path(".env")
    if not env_path.exists():
        return ""
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == name:
            return value.strip().strip('"').strip("'")
    return ""
