from __future__ import annotations

import unittest
from typing import Generic, TypeVar

from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.api.dependencies import RepositoryBundle, get_current_user, get_repositories
from app.domain.models import Itinerary, ItineraryStatus, TravelPreferences
from app.main import app
from app.services.auth import VerifiedUser
from app.services.repositories import AuditLogEntry


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


class ApiRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.preferences = InMemoryPreferencesRepository()
        self.itineraries = InMemoryItineraryRepository()
        self.audit_logs: InMemoryRepository[AuditLogEntry] = InMemoryRepository()
        self.repositories = RepositoryBundle(
            users=InMemoryRepository(),  # type: ignore[arg-type]
            preferences=self.preferences,  # type: ignore[arg-type]
            itineraries=self.itineraries,  # type: ignore[arg-type]
            audit_logs=self.audit_logs,  # type: ignore[arg-type]
        )
        app.dependency_overrides[get_current_user] = lambda: VerifiedUser(
            uid="user-1",
            email="alice@example.com",
        )
        app.dependency_overrides[get_repositories] = lambda: self.repositories
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


if __name__ == "__main__":
    unittest.main()
