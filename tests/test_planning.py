from __future__ import annotations

from datetime import date, time
import unittest

from app.domain.models import (
    DayPlan,
    DayRule,
    Itinerary,
    ItineraryStatus,
    PlaceStop,
    SourceConfidence,
    TravelPreferences,
    TripBrief,
)
from app.services.maps import CandidatePlace, Coordinates
from app.services.planning import (
    ADKPlanningWorkflowService,
    ChatAgentService,
    GroundedCitation,
    GroundedSearchCandidate,
    _planner_prompt,
)
from app.services.booking_calls import BookingCallService


class FakeMapsClient:
    def text_search(self, query: str, *, region: str, max_result_count: int = 6):
        return [
            CandidatePlace(
                place_id=f"place-{query}",
                name=f"{query.title()} Place",
                formatted_address=f"{region} address",
                primary_type="restaurant",
                google_maps_uri="https://maps.google.com/?cid=123",
                latitude=1.29,
                longitude=103.85,
            )
        ]

    def geocode(self, address: str) -> Coordinates:
        return Coordinates(latitude=1.29, longitude=103.85)

    def current_weather(self, coordinates: Coordinates) -> dict[str, object]:
        return {"weatherCondition": {"description": {"text": "Warm"}}}


class FakePlannerClient:
    def __init__(self) -> None:
        self.search_candidates = []

    def generate_plan(self, *, brief, preferences, candidates, search_candidates=None, weather):
        self.search_candidates = search_candidates or []
        return {
            "title": "Singapore Food Weekend",
            "days": [
                {
                    "day_number": 1,
                    "start_location": "Hotel",
                    "end_location": "Hotel",
                    "start_time": "10:00",
                    "end_time": "18:00",
                    "stops": [
                        {
                            "name": "Maxwell Food Centre",
                            "suggested_order": 1,
                            "time_window": "11:00-12:30",
                            "what_to_do": "Try hawker dishes.",
                            "explanation": "Matches food preference and candidate evidence.",
                            "category": "food",
                            "confidence": "high",
                            "travel_time_assumption_minutes": 15,
                        }
                    ],
                    "backup_options": ["Chinatown Complex"],
                }
            ],
        }


class SocialOnlyLowConfidencePlannerClient:
    def generate_plan(self, *, brief, preferences, candidates, weather):
        return {
            "title": "Exploratory Trip",
            "days": [
                {
                    "day_number": 1,
                    "start_location": "Hotel",
                    "end_location": "Hotel",
                    "start_time": "10:00",
                    "end_time": "18:00",
                    "stops": [
                        {
                            "name": "Rumored Cafe",
                            "suggested_order": 1,
                            "what_to_do": "Check it out.",
                            "explanation": "Only trending online.",
                            "category": "cafe",
                            "confidence": "low",
                        }
                    ],
                }
            ],
        }


class FakeChatModels:
    def generate_content(self, *, model, contents):  # noqa: ANN001
        class Response:
            text = """
            {
              "agent_message": "Added a garden stop in that gap.",
              "action": "insert_stop",
              "new_stop": {
                "name": "Villa Borghese Gardens",
                "suggested_order": 1,
                "time_window": "11:00",
                "what_to_do": "Walk through the gardens.",
                "travel_time_assumption_minutes": 10
              },
              "insert_before_index": 99
            }
            """

        return Response()


class FakeChatClient:
    models = FakeChatModels()


class FakeSearchClient:
    def search(self, *, agent_name, brief, preferences, focus, max_candidates=4):
        return [
            GroundedSearchCandidate(
                name="National Gallery Singapore",
                category="culture",
                match_reason=f"{agent_name} found a photography-friendly museum.",
                confidence=SourceConfidence.HIGH,
                citations=[
                    GroundedCitation(
                        title="Official gallery site",
                        url="https://www.nationalgallery.sg",
                    )
                ],
            )
        ]


class NoopSearchClient:
    def search(self, *, agent_name, brief, preferences, focus, max_candidates=4):
        return []


class FakeBookingMapsClient:
    def find_phone_number(self, query: str, *, region: str = "") -> str | None:
        return "+15551234567"


class StaticChatModels:
    def __init__(self, text: str) -> None:
        self.text = text

    def generate_content(self, *, model, contents):  # noqa: ANN001
        class Response:
            pass

        response = Response()
        response.text = self.text
        return response


class StaticChatClient:
    def __init__(self, text: str) -> None:
        self.models = StaticChatModels(text)


class PlanningWorkflowTests(unittest.TestCase):
    def test_generate_itinerary_builds_validated_itinerary_with_adk_agent_names(self) -> None:
        service = ADKPlanningWorkflowService(
            maps_client=FakeMapsClient(),  # type: ignore[arg-type]
            planner_client=FakePlannerClient(),
            search_client=NoopSearchClient(),  # type: ignore[arg-type]
        )

        result = service.generate_itinerary(
            user_id="user-1",
            brief=TripBrief(
                region="Singapore",
                description="Food and photography",
                trip_length_days=1,
                style_interests=["food"],
            ),
            preferences=TravelPreferences(
                user_id="user-1",
                version=4,
                onboarding_required=False,
                interests=["food"],
            ),
        )

        self.assertEqual(result.itinerary.title, "Singapore Food Weekend")
        self.assertEqual(result.itinerary.preference_version, 4)
        self.assertEqual(result.itinerary.days[0].stops[0].name, "Maxwell Food Centre")
        self.assertEqual(result.recommendations[0].confidence, SourceConfidence.HIGH)
        self.assertIn("trip_intake_agent", result.agent_names)
        self.assertIn("verification_agent", result.agent_names)
        self.assertGreaterEqual(len(result.evidence), 1)
        self.assertEqual(service.agents.adk_workflows.planning_sequence.name, "planning_sequence")
        self.assertEqual(
            service.agents.adk_workflows.retrieval_parallel.name,
            "planning_retrieval_parallel",
        )

    def test_day_rules_override_planner_start_end_places_and_times(self) -> None:
        service = ADKPlanningWorkflowService(
            maps_client=FakeMapsClient(),  # type: ignore[arg-type]
            planner_client=FakePlannerClient(),
            search_client=NoopSearchClient(),  # type: ignore[arg-type]
        )

        result = service.generate_itinerary(
            user_id="user-1",
            brief=TripBrief(
                region="Singapore",
                description="Food and photography",
                trip_length_days=1,
                day_rules=[
                    DayRule(
                        start_day=1,
                        end_day=1,
                        start_date=date(2026, 7, 1),
                        end_date=date(2026, 7, 1),
                        start_place="Changi Airport",
                        end_place="Marina Bay Sands",
                        start_time=time(8, 30),
                        end_time=time(21, 15),
                    ),
                ],
            ),
            preferences=TravelPreferences(
                user_id="user-1",
                version=4,
                onboarding_required=False,
            ),
        )

        day = result.itinerary.days[0]
        self.assertEqual(day.start_location, "Changi Airport")
        self.assertEqual(day.end_location, "Marina Bay Sands")
        self.assertEqual(day.start_time, time(8, 30))
        self.assertEqual(day.end_time, time(21, 15))

    def test_parallel_grounded_search_candidates_are_deduped_and_passed_to_planner(self) -> None:
        planner = FakePlannerClient()
        service = ADKPlanningWorkflowService(
            maps_client=FakeMapsClient(),  # type: ignore[arg-type]
            planner_client=planner,
            search_client=FakeSearchClient(),  # type: ignore[arg-type]
        )

        result = service.generate_itinerary(
            user_id="user-1",
            brief=TripBrief(
                region="Singapore",
                description="Photography and culture",
                trip_length_days=1,
            ),
            preferences=TravelPreferences(
                user_id="user-1",
                onboarding_required=False,
                interests=["photography"],
            ),
        )

        self.assertEqual(len(planner.search_candidates), 1)
        self.assertEqual(planner.search_candidates[0].name, "National Gallery Singapore")
        self.assertTrue(
            any(evidence.url == "https://www.nationalgallery.sg" for evidence in result.evidence)
        )

    def test_planner_prompt_marks_description_and_day_rules_as_mandatory(self) -> None:
        brief = TripBrief(
            region="Rome",
            description="Photography-focused ruins and quiet wine bars.",
            trip_length_days=1,
            preferred_transport_modes=["DRIVING"],
            day_rules=[
                DayRule(
                    start_day=1,
                    end_day=1,
                    start_place="Hotel Artemide",
                    end_place="Roma Termini",
                    start_time=time(9, 0),
                    end_time=time(18, 0),
                ),
            ],
        )

        prompt = _planner_prompt(
            brief,
            TravelPreferences(user_id="user-1", onboarding_required=False),
            [],
            [],
            None,
        )

        self.assertIn("trip_description_priority", prompt)
        self.assertIn("Photography-focused ruins", prompt)
        self.assertIn("mandatory_day_rules", prompt)
        self.assertIn("Hotel Artemide", prompt)
        self.assertIn("Activity time_window values must account for travel time", prompt)
        self.assertIn("travel_time_assumption_minutes", prompt)
        self.assertIn("DRIVING", prompt)

    def test_chat_gap_insert_overrides_model_chosen_index(self) -> None:
        service = ChatAgentService()
        service._client = FakeChatClient()  # type: ignore[assignment]
        itinerary = Itinerary(
            id="itin-1",
            user_id="user-1",
            title="Rome",
            status=ItineraryStatus.INACTIVE,
            brief=TripBrief(region="Rome", description="Ruins", trip_length_days=1),
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
                            name="Colosseum",
                            suggested_order=1,
                            what_to_do="Explore.",
                        ),
                        PlaceStop(
                            id="stop-2",
                            name="Pantheon",
                            suggested_order=2,
                            what_to_do="Visit.",
                        ),
                    ],
                ),
            ],
        )

        result = service.process_message(
            "Add a calm garden walk here",
            itinerary,
            day_index=0,
            insert_before_index=1,
        )

        self.assertEqual(result["action"], "insert_stop")
        self.assertEqual(result["insert_before_index"], 1)
        self.assertEqual(result["new_stop"].name, "Villa Borghese Gardens")

    def test_chat_timing_transport_recommendation_and_rewrite_actions_are_validated(self) -> None:
        itinerary = _chat_itinerary()

        timing = ChatAgentService()
        timing._client = StaticChatClient(
            """
            {
              "agent_message": "Moved the Colosseum later.",
              "action": "update_timing",
              "timing_update": {"target_stop_index": 0, "time_window": "11:30 AM"}
            }
            """
        )  # type: ignore[assignment]
        timing_result = timing.process_message("Move the first stop later", itinerary, day_index=0)
        self.assertEqual(timing_result["action"], "update_timing")
        self.assertEqual(timing_result["timing_update"]["target_stop_index"], 0)

        transport = ChatAgentService()
        transport._client = StaticChatClient(
            """
            {
              "agent_message": "Switched to transit.",
              "action": "update_transport_mode",
              "transport_update": {"preferred_transport_modes": ["TRANSIT", "FLYING"]}
            }
            """
        )  # type: ignore[assignment]
        transport_result = transport.process_message("Use buses instead", itinerary, day_index=0)
        self.assertEqual(transport_result["action"], "update_transport_mode")
        self.assertEqual(
            transport_result["transport_update"]["preferred_transport_modes"],
            ["TRANSIT"],
        )

        recommend = ChatAgentService()
        recommend._client = StaticChatClient(
            """
            {
              "agent_message": "Try these nearby ideas.",
              "action": "recommend",
              "recommendations": [
                {
                  "title": "Forum viewpoint",
                  "description": "A good photo angle near the route.",
                  "confidence": "high",
                  "sources": ["Official tourism site"]
                }
              ]
            }
            """
        )  # type: ignore[assignment]
        recommend_result = recommend.process_message("Any recommendation?", itinerary, day_index=0)
        self.assertEqual(recommend_result["action"], "recommend")
        self.assertEqual(recommend_result["recommendations"][0]["confidence"], "high")

        rewrite = ChatAgentService()
        proposed = itinerary.model_copy(deep=True)
        proposed.title = "New Rome Day"
        rewrite._client = StaticChatClient(
            """
            {
              "agent_message": "Review this rewrite.",
              "action": "propose_rewrite",
              "proposal": {
                "title": "New Rome Day",
                "summary": "A gentler route.",
                "proposed_itinerary": %s
              }
            }
            """
            % proposed.model_dump_json()
        )  # type: ignore[assignment]
        rewrite_result = rewrite.process_message("Redo the itinerary", itinerary, day_index=0)
        self.assertEqual(rewrite_result["action"], "propose_rewrite")
        self.assertEqual(rewrite_result["proposal"]["proposed_itinerary"].id, itinerary.id)

    def test_chat_booking_request_returns_offer_or_instruction_without_calling(self) -> None:
        itinerary = _chat_itinerary()
        service = ChatAgentService()
        service.booking_service = BookingCallService(
            maps_client=FakeBookingMapsClient(),  # type: ignore[arg-type]
        )

        missing = service.process_message(
            "Book a table",
            itinerary,
            day_index=0,
            target_stop_index=0,
        )
        self.assertEqual(missing["action"], "booking_info")
        self.assertIn("booking_call_offer", missing)

        offer = service.process_message(
            "Book for 2 tomorrow at 7pm under Ada, callback +15550001111",
            itinerary,
            day_index=0,
            target_stop_index=0,
        )
        self.assertIn(offer["action"], {"booking_call_offer", "booking_info"})
        self.assertEqual(offer["booking_call_offer"].details.reservation_name, "Ada")
        self.assertEqual(offer["booking_call_offer"].details.venue_phone, "+15551234567")
        self.assertIn(" at 7 PM", offer["booking_call_offer"].details.reservation_datetime)

    def test_chat_booking_request_extracts_labeled_conversation_details(self) -> None:
        itinerary = _chat_itinerary()
        service = ChatAgentService()
        service.booking_service = BookingCallService(
            maps_client=FakeBookingMapsClient(),  # type: ignore[arg-type]
        )

        result = service.process_message(
            "date/time: tomorrow at 1 pm, party size: 2 people, reservation name: Benjamin, "
            "callback phone: +6512345678. This restaurant phone number has been "
            "changed to +6580241976 recently, so call this number instead",
            itinerary,
            day_index=0,
            target_stop_index=0,
        )

        offer = result["booking_call_offer"]
        self.assertEqual(offer.missing_fields, [])
        self.assertIn(" at 1 PM", offer.details.reservation_datetime)
        self.assertEqual(offer.details.party_size, 2)
        self.assertEqual(offer.details.reservation_name, "Benjamin")
        self.assertEqual(offer.details.callback_phone, "+6512345678")
        self.assertEqual(offer.details.venue_phone, "+6580241976")

    def test_chat_booking_request_reasks_for_invalid_or_incomplete_datetime(self) -> None:
        itinerary = _chat_itinerary()
        service = ChatAgentService()
        service.booking_service = BookingCallService(
            maps_client=FakeBookingMapsClient(),  # type: ignore[arg-type]
        )

        result = service.process_message(
            "date/time: 2000-01-01 19:00, party size: 2 people, reservation name: Benjamin, "
            "callback phone: +6512345678",
            itinerary,
            day_index=0,
            target_stop_index=0,
        )

        offer = result["booking_call_offer"]
        self.assertEqual(result["action"], "booking_info")
        self.assertIn("reservation_datetime", offer.missing_fields)

    def test_chat_booking_request_accepts_explicit_hotline_override(self) -> None:
        itinerary = _chat_itinerary()
        service = ChatAgentService()
        service.booking_service = BookingCallService(
            maps_client=FakeBookingMapsClient(),  # type: ignore[arg-type]
        )

        result = service.process_message(
            "Book for 3 tomorrow at 8pm under Casey, callback phone +15550001111. "
            "Use this hotline instead +15559998888.",
            itinerary,
            day_index=0,
            target_stop_index=0,
        )

        offer = result["booking_call_offer"]
        self.assertEqual(offer.details.callback_phone, "+15550001111")
        self.assertEqual(offer.details.venue_phone, "+15559998888")

    def test_chat_booking_request_does_not_confuse_callback_with_venue_phone(self) -> None:
        itinerary = _chat_itinerary()
        service = ChatAgentService()
        service.booking_service = BookingCallService(
            maps_client=FakeBookingMapsClient(),  # type: ignore[arg-type]
        )

        result = service.process_message(
            "Book for 2 tomorrow at 7pm under Ada, callback phone +15550001111. "
            "My backup number is +15559998888.",
            itinerary,
            day_index=0,
            target_stop_index=0,
        )

        offer = result["booking_call_offer"]
        self.assertEqual(offer.details.callback_phone, "+15550001111")
        self.assertEqual(offer.details.venue_phone, "+15551234567")

    def test_chat_booking_request_falls_back_when_hotline_text_is_invalid(self) -> None:
        itinerary = _chat_itinerary()
        service = ChatAgentService()
        service.booking_service = BookingCallService(
            maps_client=FakeBookingMapsClient(),  # type: ignore[arg-type]
        )

        result = service.process_message(
            "Book for 2 tomorrow at 7pm under Ada, callback phone +15550001111, "
            "hotline: main desk only.",
            itinerary,
            day_index=0,
            target_stop_index=0,
        )

        offer = result["booking_call_offer"]
        self.assertEqual(offer.details.callback_phone, "+15550001111")
        self.assertEqual(offer.details.venue_phone, "+15551234567")

    def test_chat_booking_request_asks_one_friendly_missing_question(self) -> None:
        itinerary = _chat_itinerary()
        service = ChatAgentService()

        result = service.process_message(
            "Book a table",
            itinerary,
            day_index=0,
            target_stop_index=0,
        )

        self.assertEqual(result["action"], "booking_info")
        self.assertIn("one step at a time", result["agent_message"])
        self.assertIn("When should I try to book it for?", result["agent_message"])
        self.assertNotIn("reservation_datetime", result["agent_message"])

    def test_low_confidence_output_without_supporting_evidence_is_not_persisted_as_recommendation(
        self,
    ) -> None:
        service = ADKPlanningWorkflowService(
            maps_client=FakeMapsClient(),  # type: ignore[arg-type]
            planner_client=SocialOnlyLowConfidencePlannerClient(),
            search_client=NoopSearchClient(),  # type: ignore[arg-type]
        )

        result = service.generate_itinerary(
            user_id="user-1",
            brief=TripBrief(region="Singapore", description="Cafe crawl", trip_length_days=1),
            preferences=TravelPreferences(user_id="user-1", version=1, onboarding_required=False),
        )

        self.assertEqual(len(result.recommendations), 1)
        self.assertEqual(result.recommendations[0].confidence, SourceConfidence.LOW)


def _chat_itinerary() -> Itinerary:
    return Itinerary(
        id="itin-1",
        user_id="user-1",
        title="Rome",
        status=ItineraryStatus.INACTIVE,
        brief=TripBrief(region="Rome", description="Ruins", trip_length_days=1),
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
                        name="Colosseum",
                        suggested_order=1,
                        what_to_do="Explore.",
                    ),
                    PlaceStop(
                        id="stop-2",
                        name="Pantheon",
                        suggested_order=2,
                        what_to_do="Visit.",
                    ),
                ],
            ),
        ],
    )


if __name__ == "__main__":
    unittest.main()
