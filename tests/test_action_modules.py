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
from app.services.booking_calls import BookingCallService
from app.services.booking_intake import (
    BOOKING_INTAKE_ORDER,
    BookingIntakeField,
    BookingIntakeState,
    format_readable_datetime,
    parse_natural_datetime,
    validate_future_datetime,
)
from app.services.manual_call import ManualCallRequest, ManualCallService
from app.services.stripe_commerce import (
    AgenticProviderPackageCandidate,
    AgenticProviderPackageOutput,
    LinkValidationEvidence,
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

    parsed = parse_natural_datetime(
        "next Friday at 7:30 pm",
        now=datetime(2026, 7, 5, 10, 0, tzinfo=timezone.utc),
    )
    assert parsed is not None
    assert parsed.year == 2026
    assert parsed.hour == 19
    assert parsed.minute == 30
    assert "Friday" in format_readable_datetime(parsed)


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
    router = AskAnythingRouter(agent_client=FailingAskClient())

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


def test_ask_anything_uses_agentic_answer_for_informational_request() -> None:
    router = AskAnythingRouter(agent_client=FakeAskClient())

    answer = router.classify(
        AskAnythingRequest(
            message="What should I notice when I visit?",
            venue_name="National Gallery Singapore",
            region="Singapore",
        )
    )

    assert answer.intent == AskAnythingIntent.INFORMATIONAL
    assert "National Gallery Singapore" in answer.agent_message
    assert answer.suggested_destination is None


def test_provider_commerce_requires_confirmation_and_never_exposes_secret_to_flutter() -> None:
    service = StripeCommerceService()

    assert service.assert_payment_confirmation(confirmed=False).allowed is False
    assert service.assert_payment_confirmation(confirmed=True).allowed is True
    assert service.exposes_secret_to_flutter is False


def test_provider_commerce_returns_activity_scoped_external_checkout_options() -> None:
    service = StripeCommerceService(search_client=FailingPackageSearchClient())
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
    assert all("google.com/search" not in result.checkout_url for result in response.results)
    assert all(result.source_type == "provider_discovery" for result in response.results)
    assert response.has_more is True


def test_provider_commerce_returns_agentic_source_backed_options() -> None:
    service = StripeCommerceService(
        search_client=FakePackageSearchClient(), link_validator=FakeLinkValidator()
    )
    itinerary = _commerce_itinerary()

    response = service.search_packages(
        ProviderPackageSearchRequest(
            itinerary_id=itinerary.id,
            day_index=0,
            stop_index=0,
            query="night safari tickets",
        ),
        itinerary=itinerary,
    )

    assert response.activity_name == "Singapore Zoo"
    assert len(response.results) == 1
    assert response.results[0].name == "Singapore Zoo Official Admission"
    assert response.results[0].checkout_url == "https://www.mandai.com/en/tickets.html"
    assert response.results[0].validation_summary is not None
    assert "package_search_sequence" in response.evidence_note


def test_provider_commerce_rejects_agentic_links_without_validation_evidence() -> None:
    service = StripeCommerceService(search_client=UnvalidatedPackageSearchClient())
    itinerary = _commerce_itinerary()

    response = service.search_packages(
        ProviderPackageSearchRequest(
            itinerary_id=itinerary.id,
            day_index=0,
            stop_index=0,
            query="zoo tickets",
        ),
        itinerary=itinerary,
    )

    assert response.results
    assert all(result.package_id.startswith("agentic-") is False for result in response.results)
    assert all(result.source_type == "provider_discovery" for result in response.results)


def test_booking_call_language_resolver_uses_region_and_defaults_to_english() -> None:
    service = BookingCallService(maps_client=NoopBookingMapsClient())  # type: ignore[arg-type]

    japanese = service._resolve_call_language(venue_name="Sushi Dai", region="Tokyo, Japan")
    unknown = service._resolve_call_language(venue_name="Cafe Anywhere", region="")

    assert japanese.selected_language == "Japanese"
    assert "booking_locale_resolver_agent" in japanese.rationale
    assert unknown.selected_language == "English"
    assert unknown.confidence == "low"


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


def test_provider_checkout_rejects_search_result_checkout_url() -> None:
    service = StripeCommerceService()
    request = ProviderCheckoutRequest(
        package_id="bad-1",
        checkout_url="https://www.google.com/search?q=Singapore+Zoo+tickets",
        confirmed=True,
    )

    try:
        service.prepare_checkout(request)
    except ValueError as exc:
        assert "valid provider page" in str(exc)
    else:
        raise AssertionError("Expected search result checkout URL to be rejected.")


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


class FakeLinkValidator:
    def validate(self, *, url: str, activity_name: str, region: str) -> LinkValidationEvidence:
        return LinkValidationEvidence(
            valid=True,
            summary="Fetched and checked test page content for activity relevance.",
            source_type="official",
            relevance_rationale=f"{activity_name} appears on the fetched page.",
            final_url=url,
        )


class FakePackageSearchClient:
    def search(self, *, activity_name: str, region: str, query: str, limit: int):
        return AgenticProviderPackageOutput(
            candidates=[
                AgenticProviderPackageCandidate(
                    name=f"{activity_name} Official Admission",
                    description="Official admission ticket from the venue operator.",
                    provider_name="Mandai Wildlife Reserve",
                    checkout_url="https://www.mandai.com/en/tickets.html",
                    source_url="https://www.mandai.com/en/tickets.html",
                    confidence="high",
                    price_summary="Shown by provider",
                    cancellation_summary="Provider terms apply",
                    validation_summary=(
                        "Grounded result points to the official Mandai ticketing page and "
                        "was not a not-found or error page."
                    ),
                    source_type="official",
                    relevance_rationale="Mandai is the operator for Singapore Zoo tickets.",
                )
            ],
            evidence_note="Mocked grounded package result.",
        )


class UnvalidatedPackageSearchClient:
    def search(self, *, activity_name: str, region: str, query: str, limit: int):
        return AgenticProviderPackageOutput(
            candidates=[
                AgenticProviderPackageCandidate(
                    name=f"{activity_name} Suspicious Admission",
                    description="Looks plausible but no validation evidence was supplied.",
                    provider_name="Unknown Tickets",
                    checkout_url="https://www.google.com/search?q=Singapore+Zoo+tickets",
                    source_url="https://www.google.com/search?q=Singapore+Zoo+tickets",
                    confidence="high",
                )
            ],
            evidence_note="Mocked invalid grounded package result.",
        )


class FailingPackageSearchClient:
    def search(self, *, activity_name: str, region: str, query: str, limit: int):
        raise RuntimeError("grounding unavailable")


class FakeAskClient:
    def answer(self, request: AskAnythingRequest):
        from app.services.ask_anything import AskAnythingResponse

        return AskAnythingResponse(
            intent=AskAnythingIntent.INFORMATIONAL,
            agent_message=f"Look for the architecture and current exhibitions at {request.venue_name}.",
        )


class FailingAskClient:
    def answer(self, request: AskAnythingRequest):
        raise RuntimeError("agent unavailable")


class NoopBookingMapsClient:
    def find_phone_number(self, query: str, *, region: str = "") -> str | None:
        return None
