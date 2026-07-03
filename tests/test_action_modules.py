from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.actions import (
    ActivityActionContext,
    ActivityActionDestination,
    ActivityActionsService,
)
from app.services.ask_anything import AskAnythingIntent, AskAnythingRequest, AskAnythingRouter
from app.services.booking_intake import (
    BOOKING_INTAKE_ORDER,
    BookingIntakeField,
    BookingIntakeState,
    validate_future_datetime,
)
from app.services.manual_call import ManualCallRequest, ManualCallService
from app.services.stripe_commerce import StripeCommerceService


def test_activity_actions_are_explicit_and_side_effect_guarded() -> None:
    options = ActivityActionsService().options_for_activity(
        ActivityActionContext(
            itinerary_id="itin-1",
            day_index=0,
            stop_index=1,
            venue_name="Colosseum",
        )
    )

    assert [option.destination for option in options] == [
        ActivityActionDestination.CALL_VENUE,
        ActivityActionDestination.PURCHASE_WITH_STRIPE,
        ActivityActionDestination.ASK_AGENT_ANYTHING,
    ]
    assert options[0].requires_confirmation_before_side_effect is True
    assert options[1].requires_confirmation_before_side_effect is True
    assert options[2].requires_confirmation_before_side_effect is False


def test_booking_intake_order_and_future_datetime_validation() -> None:
    assert BOOKING_INTAKE_ORDER == (
        BookingIntakeField.VENUE_CONTACT,
        BookingIntakeField.REQUESTOR_NAME,
        BookingIntakeField.RESERVATION_DATETIME,
        BookingIntakeField.PARTY_SIZE,
        BookingIntakeField.REMARKS,
        BookingIntakeField.SUMMARY_CONFIRMATION,
    )

    state = BookingIntakeState(venue_name="Test Venue")
    assert state.next_field() == BookingIntakeField.VENUE_CONTACT

    future = datetime.now(timezone.utc) + timedelta(days=1)
    past = datetime.now(timezone.utc) - timedelta(days=1)
    assert validate_future_datetime(future).valid is True
    assert validate_future_datetime(past).valid is False
    assert validate_future_datetime(None).valid is False


def test_manual_call_service_can_prepare_script_without_contact() -> None:
    response = ManualCallService().prepare_response(
        ManualCallRequest(
            venue_name="Test Venue",
            remarks="I want to ask about a table for two.",
        ),
        include_script=True,
    )

    assert response.contact_found is False
    assert response.venue_contact is None
    assert response.script is not None
    assert "table for two" in response.script


def test_ask_anything_routes_booking_and_purchase_without_side_effects() -> None:
    router = AskAnythingRouter()

    booking = router.classify(
        AskAnythingRequest(message="Can you book a table?", venue_name="Test Venue")
    )
    assert booking.intent == AskAnythingIntent.BOOKING
    assert booking.suggested_destination == ActivityActionDestination.CALL_VENUE

    purchase = router.classify(
        AskAnythingRequest(message="Can I buy tickets here?", venue_name="Test Venue")
    )
    assert purchase.intent == AskAnythingIntent.PURCHASE
    assert purchase.suggested_destination == ActivityActionDestination.PURCHASE_WITH_STRIPE


def test_stripe_commerce_requires_confirmation_and_never_exposes_secret_to_flutter() -> None:
    service = StripeCommerceService()

    assert service.assert_payment_confirmation(confirmed=False).allowed is False
    assert service.assert_payment_confirmation(confirmed=True).allowed is True
    assert service.exposes_secret_to_flutter is False
