from __future__ import annotations

import unittest
from typing import Generic, TypeVar

from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.api.dependencies import (
    RepositoryBundle,
    get_active_event_service,
    get_current_user,
    get_repositories,
)
from app.api.dependencies import get_planning_service
from app.domain.models import (
    DayPlan,
    DynamicBehaviorPreferences,
    Itinerary,
    ItineraryStatus,
    PlaceStop,
    Recommendation,
    RecoveryProposal,
    SourceConfidence,
    SourceEvidence,
    SourceType,
    TravelPreferences,
)
from app.main import app
from app.services.active_events import ActiveEventWorkflowService
from app.services.auth import VerifiedUser
from app.services.repositories import AuditLogEntry
from app.services.planning import PlanningResult


T = TypeVar("T", bound=BaseModel)


class InMemoryRepository(Generic[T]):
    def __init__(self) -> None:
        self.items: dict[str, T] = {}

    def create(self, doc_id: str, model: T) -> T:
        self.items[doc_id] = model
        return model

    def get(self, doc_id: str) -> T | None:
        return self.items.get(doc_id)

    def update(self, doc_id: str, model: T) -> T:
        self.items[doc_id] = model
        return model

    def delete(self, doc_id: str) -> None:
        self.items.pop(doc_id, None)

    def query_by_field(self, field: str, value: object) -> list[T]:
        return [item for item in self.items.values() if getattr(item, field) == value]

    def list_all(self) -> list[T]:
        return list(self.items.values())


class InMemoryPreferencesRepository(InMemoryRepository[TravelPreferences]):
    def get_by_user(self, user_id: str) -> TravelPreferences | None:
        return self.get(user_id) or next(
            (item for item in self.items.values() if item.user_id == user_id),
            None,
        )


class InMemoryItineraryRepository(InMemoryRepository[Itinerary]):
    def find_by_user(self, user_id: str) -> list[Itinerary]:
        return self.query_by_field("user_id", user_id)

    def find_active(self, user_id: str) -> Itinerary | None:
        return next(
            (
                item
                for item in self.find_by_user(user_id)
                if item.status == ItineraryStatus.ACTIVE
            ),
            None,
        )


class InMemoryDynamicPreferencesRepository(InMemoryRepository[DynamicBehaviorPreferences]):
    def get_by_itinerary(self, itinerary_id: str) -> DynamicBehaviorPreferences | None:
        return self.get(itinerary_id)


class ApiRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.preferences = InMemoryPreferencesRepository()
        self.itineraries = InMemoryItineraryRepository()
        self.dynamic_preferences = InMemoryDynamicPreferencesRepository()
        self.evidence: InMemoryRepository[SourceEvidence] = InMemoryRepository()
        self.recommendations: InMemoryRepository[Recommendation] = InMemoryRepository()
        self.recovery_proposals: InMemoryRepository[RecoveryProposal] = InMemoryRepository()
        self.audit_logs: InMemoryRepository[AuditLogEntry] = InMemoryRepository()
        self.repositories = RepositoryBundle(
            users=InMemoryRepository(),  # type: ignore[arg-type]
            preferences=self.preferences,  # type: ignore[arg-type]
            itineraries=self.itineraries,  # type: ignore[arg-type]
            dynamic_preferences=self.dynamic_preferences,  # type: ignore[arg-type]
            evidence=self.evidence,  # type: ignore[arg-type]
            recommendations=self.recommendations,  # type: ignore[arg-type]
            recovery_proposals=self.recovery_proposals,  # type: ignore[arg-type]
            audit_logs=self.audit_logs,  # type: ignore[arg-type]
        )
        app.dependency_overrides[get_current_user] = lambda: VerifiedUser(
            uid="user-1",
            email="alice@example.com",
        )
        app.dependency_overrides[get_repositories] = lambda: self.repositories
        app.dependency_overrides[get_planning_service] = lambda: FakePlanningService()
        app.dependency_overrides[get_active_event_service] = lambda: ActiveEventWorkflowService(
            publisher=FakePublisher()
        )
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_preferences_update_allows_itinerary_creation(self) -> None:
        prefs_response = self.client.put(
            "/v1/preferences",
            json={"pace": "relaxed", "interests": ["food", "photography"]},
        )
        self.assertEqual(prefs_response.status_code, 200)
        self.assertFalse(prefs_response.json()["onboarding_required"])

        create_response = self.client.post("/v1/itineraries", json=itinerary_payload())
        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(create_response.json()["status"], "INACTIVE")
        self.assertEqual(create_response.json()["preference_version"], 2)

        list_response = self.client.get("/v1/itineraries")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(list_response.json()), 1)

    def test_itinerary_creation_requires_completed_onboarding(self) -> None:
        response = self.client.post("/v1/itineraries", json=itinerary_payload())

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "onboarding_required")

    def test_generate_itinerary_persists_planned_itinerary_evidence_and_recommendations(self) -> None:
        self.preferences.create(
            "user-1",
            TravelPreferences(
                user_id="user-1",
                version=7,
                onboarding_required=False,
                interests=["food"],
            ),
        )

        response = self.client.post("/v1/itineraries/generate", json=itinerary_payload("Generated"))

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["title"], "Generated")
        self.assertEqual(body["preference_version"], 7)
        self.assertEqual(len(body["days"]), 1)
        self.assertEqual(len(self.itineraries.items), 1)
        self.assertEqual(len(self.evidence.items), 1)
        self.assertEqual(len(self.recommendations.items), 1)

    def test_start_requires_replacement_confirmation_then_switches_active_itinerary(self) -> None:
        self.preferences.create(
            "user-1",
            TravelPreferences(user_id="user-1", version=3, onboarding_required=False),
        )
        first = self.client.post("/v1/itineraries", json=itinerary_payload("First")).json()
        second = self.client.post("/v1/itineraries", json=itinerary_payload("Second")).json()

        start_first = self.client.post(f"/v1/itineraries/{first['id']}/start")
        self.assertEqual(start_first.status_code, 200)

        start_second_without_confirmation = self.client.post(
            f"/v1/itineraries/{second['id']}/start"
        )
        self.assertEqual(start_second_without_confirmation.status_code, 409)
        self.assertEqual(
            start_second_without_confirmation.json()["detail"]["code"],
            "replacement_confirmation_required",
        )

        start_second = self.client.post(
            f"/v1/itineraries/{second['id']}/start?confirm_replace=true"
        )
        self.assertEqual(start_second.status_code, 200)
        stored_first = self.itineraries.get(first["id"])
        stored_second = self.itineraries.get(second["id"])
        self.assertEqual(stored_first.status if stored_first else None, ItineraryStatus.INACTIVE)
        self.assertEqual(stored_second.status if stored_second else None, ItineraryStatus.ACTIVE)

    def test_stop_complete_save_pattern_export_and_delete(self) -> None:
        self.preferences.create(
            "user-1",
            TravelPreferences(user_id="user-1", version=5, onboarding_required=False),
        )
        itinerary = self.client.post("/v1/itineraries", json=itinerary_payload()).json()

        self.assertEqual(self.client.post(f"/v1/itineraries/{itinerary['id']}/start").status_code, 200)
        self.assertEqual(self.client.post(f"/v1/itineraries/{itinerary['id']}/stop").status_code, 200)
        self.assertEqual(
            self.client.post(f"/v1/itineraries/{itinerary['id']}/complete").status_code,
            200,
        )

        pattern_response = self.client.post(
            f"/v1/itineraries/{itinerary['id']}/preference-pattern"
        )
        self.assertEqual(pattern_response.status_code, 200)
        self.assertEqual(len(pattern_response.json()["saved_itinerary_patterns"]), 1)

        export_response = self.client.post(f"/v1/itineraries/{itinerary['id']}/export")
        self.assertEqual(export_response.status_code, 200)
        self.assertEqual(export_response.json()["status"], "accepted")

        delete_response = self.client.delete(f"/v1/itineraries/{itinerary['id']}")
        self.assertEqual(delete_response.status_code, 200)
        self.assertIsNone(self.itineraries.get(itinerary["id"]))

    def test_location_event_rejects_inactive_itinerary_before_publishing(self) -> None:
        self.preferences.create(
            "user-1",
            TravelPreferences(user_id="user-1", version=2, onboarding_required=False),
        )
        itinerary = self.client.post("/v1/itineraries", json=itinerary_payload()).json()

        response = self.client.post(
            f"/v1/itineraries/{itinerary['id']}/location-events",
            json=location_event_payload(deviation_detected=True),
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["detail"]["code"],
            "inactive_itinerary_event_rejected",
        )
        self.assertEqual(len(self.dynamic_preferences.items), 0)
        self.assertEqual(len(self.recovery_proposals.items), 0)

        self.client.post(f"/v1/itineraries/{itinerary['id']}/complete")
        completed_response = self.client.post(
            f"/v1/itineraries/{itinerary['id']}/location-events",
            json=location_event_payload(deviation_detected=True),
        )
        self.assertEqual(completed_response.status_code, 409)

    def test_active_location_event_updates_dynamic_preferences_and_recovery_contract(self) -> None:
        self.preferences.create(
            "user-1",
            TravelPreferences(user_id="user-1", version=4, onboarding_required=False),
        )
        itinerary = self.client.post("/v1/itineraries", json=itinerary_payload()).json()
        self.client.post(f"/v1/itineraries/{itinerary['id']}/start")

        event_response = self.client.post(
            f"/v1/itineraries/{itinerary['id']}/location-events",
            json=location_event_payload(deviation_detected=True),
        )

        self.assertEqual(event_response.status_code, 200)
        body = event_response.json()
        self.assertTrue(body["accepted"])
        self.assertEqual(body["published_event_id"], "fake-message-id")
        self.assertEqual(body["dynamic_preference_version"], 2)
        proposal = body["recovery_proposal"]
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal["status"], "PENDING")
        self.assertTrue(proposal["requires_user_acceptance"])
        self.assertEqual(len(self.recovery_proposals.items), 1)

        proposal_id = proposal["id"]
        accept_response = self.client.post(
            f"/v1/itineraries/{itinerary['id']}/recovery-proposals/{proposal_id}/accept"
        )
        self.assertEqual(accept_response.status_code, 200)
        self.assertEqual(accept_response.json()["status"], "ACCEPTED")


def itinerary_payload(title: str = "Singapore Food Trip") -> dict[str, object]:
    return {
        "title": title,
        "brief": {
            "region": "Singapore",
            "description": "Food, neighborhoods, and photography",
            "trip_length_days": 2,
            "style_interests": ["food", "photography"],
            "constraints": ["avoid early mornings"],
        },
        "days": [],
    }


def location_event_payload(deviation_detected: bool = False) -> dict[str, object]:
    return {
        "location": {
            "latitude": 1.3521,
            "longitude": 103.8198,
            "accuracy_meters": 20,
        },
        "occurred_at": "2026-06-21T10:00:00+08:00",
        "speed_meters_per_second": 1.2,
        "context_signal": "off_route" if deviation_detected else "movement",
        "deviation_detected": deviation_detected,
    }


class FakePublisher:
    def publish(self, event) -> str:
        return "fake-message-id"


class FakePlanningService:
    def generate_itinerary(
        self,
        *,
        user_id: str,
        brief,
        preferences: TravelPreferences,
    ) -> PlanningResult:
        evidence = SourceEvidence(
            source_type=SourceType.GOOGLE_PLACES,
            title="Maxwell Food Centre",
            confidence=SourceConfidence.HIGH,
        )
        recommendation = Recommendation(
            id="rec-generated-1",
            title="Maxwell Food Centre",
            category="food",
            explanation="It matches the user's food preference and is a known hawker centre.",
            confidence=SourceConfidence.HIGH,
            evidence=[evidence],
        )
        itinerary = Itinerary(
            id="itin-generated",
            user_id=user_id,
            title="Generated",
            brief=brief,
            preference_version=preferences.version,
            days=[
                DayPlan(
                    day_number=1,
                    start_location="Hotel",
                    end_location="Hotel",
                    start_time="09:00",
                    end_time="18:00",
                    stops=[
                        PlaceStop(
                            id="stop-generated-1",
                            name="Maxwell Food Centre",
                            suggested_order=1,
                            what_to_do="Try hawker food.",
                            recommendations=[recommendation],
                        )
                    ],
                )
            ],
        )
        return PlanningResult(
            itinerary=itinerary,
            evidence=[evidence],
            recommendations=[recommendation],
            agent_names=["trip_intake_agent", "place_discovery_agent", "verification_agent"],
        )


if __name__ == "__main__":
    unittest.main()
