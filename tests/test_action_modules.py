from __future__ import annotations

from datetime import datetime, timedelta, timezone

from datetime import time

from app.domain.models import DayPlan, Itinerary, PlaceStop, TripBrief
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
from app.services.stripe_commerce import (
    ProviderCheckoutRequest,
    ProviderPackageSearchRequest,
    StripeCommerceService,
)


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
        ActivityActionDestination.BOOK_OR_BUY_PACKAGES,
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
    assert purchase.suggested_destination == ActivityActionDestination.BOOK_OR_BUY_PACKAGES


def test_provider_commerce_requires_confirmation_and_never_exposes_secret_to_flutter() -> None:
    service = StripeCommerceService()

    assert service.assert_payment_confirmation(confirmed=False).allowed is False
    assert service.assert_payment_confirmation(confirmed=True).allowed is True
    assert service.exposes_secret_to_flutter is False


def test_provider_commerce_returns_activity_scoped_external_checkout_options() -> None:
    service = StripeCommerceService()
    itinerary = _commerce_itinerary()

    response = service.search_packages(
        ProviderPackageSearchRequest(
            itinerary_id=itinerary.id,
            day_index=0,
            stop_index=0,
            query="family ticket",
        ),
        itinerary=itinerary,
    )

    assert response.activity_name == "Singapore Zoo"
    assert len(response.results) == 5
    assert all("Singapore Zoo" in result.name for result in response.results)
    assert all(result.checkout_url.startswith("https://") for result in response.results)
    assert response.has_more is True


def test_provider_checkout_requires_explicit_confirmation() -> None:
    service = StripeCommerceService()

    request = ProviderCheckoutRequest(
        package_id="official-1",
        checkout_url="https://example.com/checkout",
        confirmed=False,
    )

    try:
        service.prepare_checkout(request)
    except ValueError as exc:
        assert "confirmation" in str(exc)
    else:
        raise AssertionError("Expected checkout guardrail to reject unconfirmed checkout.")

    response = service.prepare_checkout(request.model_copy(update={"confirmed": True}))
    assert response.checkout_url == "https://example.com/checkout"


def _commerce_itinerary() -> Itinerary:
    return Itinerary(
        id="itin-commerce",
        user_id="user-1",
        title="Singapore",
        brief=TripBrief(
            region="Singapore",
            description="family attractions",
            trip_length_days=1,
        ),
        preference_version=1,
        days=[
            DayPlan(
                day_number=1,
                start_location="Hotel",
                end_location="Hotel",
                start_time=time(9, 0),
                end_time=time(18, 0),
                stops=[
                    PlaceStop(
                        id="stop-1",
                        name="Singapore Zoo",
                        what_to_do="Visit the zoo.",
                        time_window="09:00",
                        suggested_order=1,
                    )
                ],
            )
        ],
    )
