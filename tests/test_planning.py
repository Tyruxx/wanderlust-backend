from __future__ import annotations

import unittest

from app.domain.models import SourceConfidence, TravelPreferences, TripBrief
from app.services.maps import CandidatePlace, Coordinates
from app.services.planning import ADKPlanningWorkflowService


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
    def generate_plan(self, *, brief, preferences, candidates, weather):
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


class PlanningWorkflowTests(unittest.TestCase):
    def test_generate_itinerary_builds_validated_itinerary_with_adk_agent_names(self) -> None:
        service = ADKPlanningWorkflowService(
            maps_client=FakeMapsClient(),  # type: ignore[arg-type]
            planner_client=FakePlannerClient(),
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

    def test_low_confidence_output_without_supporting_evidence_is_not_persisted_as_recommendation(self) -> None:
        service = ADKPlanningWorkflowService(
            maps_client=FakeMapsClient(),  # type: ignore[arg-type]
            planner_client=SocialOnlyLowConfidencePlannerClient(),
        )

        result = service.generate_itinerary(
            user_id="user-1",
            brief=TripBrief(region="Singapore", description="Cafe crawl", trip_length_days=1),
            preferences=TravelPreferences(user_id="user-1", version=1, onboarding_required=False),
        )

        self.assertEqual(len(result.recommendations), 1)
        self.assertEqual(result.recommendations[0].confidence, SourceConfidence.LOW)


if __name__ == "__main__":
    unittest.main()
