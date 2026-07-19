from __future__ import annotations

import os
import unittest
from unittest.mock import patch
from typing import Generic, TypeVar

from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.api.dependencies import (
    RepositoryBundle,
    get_active_event_service,
    get_booking_service,
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
from app.services.booking_calls import BookingCallService
from app.core.settings import get_settings
from app.services.maps import Coordinates
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
            (item for item in self.find_by_user(user_id) if item.status == ItineraryStatus.ACTIVE),
            None,
        )


class InMemoryDynamicPreferencesRepository(InMemoryRepository[DynamicBehaviorPreferences]):
    def get_by_itinerary(self, itinerary_id: str) -> DynamicBehaviorPreferences | None:
        return self.get(itinerary_id)


class ApiRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_snapshot = {
            key: os.environ.get(key)
            for key in (
                "TWILIO_ACCOUNT_SID",
                "TWILIO_AUTH_TOKEN",
                "TWILIO_FROM_NUMBER",
                "PUBLIC_BACKEND_BASE_URL",
                "GOOGLE_API_KEY",
            )
        }
        for key in self._env_snapshot:
            os.environ[key] = ""
        get_settings.cache_clear()
        self.preferences = InMemoryPreferencesRepository()
        self.itineraries = InMemoryItineraryRepository()
        self.dynamic_preferences = InMemoryDynamicPreferencesRepository()
        self.evidence: InMemoryRepository[SourceEvidence] = InMemoryRepository()
        self.recommendations: InMemoryRepository[Recommendation] = InMemoryRepository()
        self.recovery_proposals: InMemoryRepository[RecoveryProposal] = InMemoryRepository()
        self.audit_logs: InMemoryRepository[AuditLogEntry] = InMemoryRepository()
        self.repositories = RepositoryBundle(
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
        app.dependency_overrides[get_booking_service] = lambda: BookingCallService(
            maps_client=FakeBookingMapsClient()  # type: ignore[arg-type]
        )
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        for key, value in self._env_snapshot.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()

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

    def test_generate_itinerary_persists_planned_itinerary_evidence_and_recommendations(
        self,
    ) -> None:
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

    def test_chat_timing_mutation_persists_and_rewrite_proposal_does_not(self) -> None:
        self.preferences.create(
            "user-1",
            TravelPreferences(user_id="user-1", version=2, onboarding_required=False),
        )
        itinerary = self.client.post("/v1/itineraries", json=chat_itinerary_payload()).json()

        with patch("app.services.planning.ChatAgentService", lambda: FakeTimingChatAgent()):
            timing_response = self.client.post(
                f"/v1/itineraries/{itinerary['id']}/chat",
                json={"message": "Move the first stop later", "day_index": 0},
            )

        self.assertEqual(timing_response.status_code, 200)
        self.assertEqual(timing_response.json()["action"], "update_timing")
        stored = self.itineraries.get(itinerary["id"])
        self.assertEqual(stored.days[0].stops[0].time_window if stored else None, "11:30 AM")

        with patch("app.services.planning.ChatAgentService", lambda: FakeRewriteChatAgent()):
            proposal_response = self.client.post(
                f"/v1/itineraries/{itinerary['id']}/chat",
                json={"message": "Redo the whole itinerary", "day_index": 0},
            )

        self.assertEqual(proposal_response.status_code, 200)
        self.assertEqual(proposal_response.json()["action"], "propose_rewrite")
        stored_after_proposal = self.itineraries.get(itinerary["id"])
        self.assertEqual(
            stored_after_proposal.days[0].stops[0].time_window if stored_after_proposal else None,
            "11:30 AM",
        )

    def test_booking_call_requires_confirmation_and_falls_back_when_unconfigured(self) -> None:
        self.preferences.create(
            "user-1",
            TravelPreferences(user_id="user-1", version=2, onboarding_required=False),
        )
        itinerary = self.client.post("/v1/itineraries", json=chat_itinerary_payload()).json()
        payload = {
            "day_index": 0,
            "stop_index": 0,
            "confirmed": False,
            "details": {
                "venue_name": "Colosseum",
                "venue_phone": "+15551234567",
                "reservation_datetime": "tomorrow at 7pm",
                "party_size": 2,
                "reservation_name": "Ada",
                "callback_phone": "+15550001111",
            },
        }

        rejected = self.client.post(
            f"/v1/itineraries/{itinerary['id']}/booking-calls",
            json=payload,
        )
        self.assertEqual(rejected.status_code, 400)

        payload["confirmed"] = True
        response = self.client.post(
            f"/v1/itineraries/{itinerary['id']}/booking-calls",
            json=payload,
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()["call"]
        self.assertEqual(body["status"], "fallback_required")
        self.assertIsNone(body["details"])
        self.assertIn("Colosseum", body["fallback_instructions"])

    def test_booking_call_websocket_receives_simulated_twilio_statuses(self) -> None:
        self._configure_call_env()
        booking_service = FakeApiBookingCallService()
        app.dependency_overrides[get_booking_service] = lambda: booking_service
        self.preferences.create(
            "user-1",
            TravelPreferences(user_id="user-1", version=2, onboarding_required=False),
        )
        itinerary = self.client.post("/v1/itineraries", json=chat_itinerary_payload()).json()
        payload = {
            "day_index": 0,
            "stop_index": 0,
            "confirmed": True,
            "details": {
                "venue_name": "Colosseum",
                "venue_phone": "+15551234567",
                "reservation_datetime": "Monday, July 6, 2026 at 7 PM",
                "party_size": 2,
                "reservation_name": "Ada",
                "callback_phone": "+15550001111",
            },
        }

        started = self.client.post(
            f"/v1/itineraries/{itinerary['id']}/booking-calls",
            json=payload,
        )
        self.assertEqual(started.status_code, 200)
        call = started.json()["call"]
        self.assertEqual(call["status"], "queued")

        with self.client.websocket_connect(
            f"/v1/booking-calls/ws/{call['call_id']}",
            headers={"X-User-Id": "user-1"},
        ) as websocket:
            self.assertEqual(websocket.receive_json()["status"], "queued")
            for twilio_status, expected in (
                ("ringing", "ringing"),
                ("in-progress", "in_progress"),
            ):
                response = self.client.post(
                    "/v1/booking-calls/twilio-status",
                    data={"CallSid": "CA1234567890", "CallStatus": twilio_status},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(websocket.receive_json()["status"], expected)

            self.assertIsNotNone(booking_service.stream_token)
            response = self.client.post(
                f"/v1/booking-calls/voice-menu/{booking_service.stream_token}",
                data={"Digits": "2"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(websocket.receive_json()["status"], "booked")

    def test_booking_call_websocket_reports_failed_when_completed_without_confirmation(self) -> None:
        self._configure_call_env()
        booking_service = FakeApiBookingCallService()
        app.dependency_overrides[get_booking_service] = lambda: booking_service
        self.preferences.create(
            "user-1",
            TravelPreferences(user_id="user-1", version=2, onboarding_required=False),
        )
        itinerary = self.client.post("/v1/itineraries", json=chat_itinerary_payload()).json()
        started = self.client.post(
            f"/v1/itineraries/{itinerary['id']}/booking-calls",
            json={
                "day_index": 0,
                "stop_index": 0,
                "confirmed": True,
                "details": {
                    "venue_name": "Colosseum",
                    "venue_phone": "+15551234567",
                    "reservation_datetime": "Monday, July 6, 2026 at 7 PM",
                    "party_size": 2,
                    "reservation_name": "Ada",
                    "callback_phone": "+15550001111",
                },
            },
        )
        call = started.json()["call"]

        with self.client.websocket_connect(
            f"/v1/booking-calls/ws/{call['call_id']}",
            headers={"X-User-Id": "user-1"},
        ) as websocket:
            self.assertEqual(websocket.receive_json()["status"], "queued")
            response = self.client.post(
                "/v1/booking-calls/twilio-status",
                data={"CallSid": "CA1234567890", "CallStatus": "completed"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(websocket.receive_json()["status"], "failed")

    def test_booking_call_websocket_reports_declined_from_voice_menu(self) -> None:
        self._configure_call_env()
        booking_service = FakeApiBookingCallService()
        app.dependency_overrides[get_booking_service] = lambda: booking_service
        self.preferences.create(
            "user-1",
            TravelPreferences(user_id="user-1", version=2, onboarding_required=False),
        )
        itinerary = self.client.post("/v1/itineraries", json=chat_itinerary_payload()).json()
        started = self.client.post(
            f"/v1/itineraries/{itinerary['id']}/booking-calls",
            json={
                "day_index": 0,
                "stop_index": 0,
                "confirmed": True,
                "details": {
                    "venue_name": "Colosseum",
                    "venue_phone": "+15551234567",
                    "reservation_datetime": "Monday, July 6, 2026 at 7 PM",
                    "party_size": 2,
                    "reservation_name": "Ada",
                    "callback_phone": "+15550001111",
                },
            },
        )
        call = started.json()["call"]

        with self.client.websocket_connect(
            f"/v1/booking-calls/ws/{call['call_id']}",
            headers={"X-User-Id": "user-1"},
        ) as websocket:
            self.assertEqual(websocket.receive_json()["status"], "queued")
            self.assertIsNotNone(booking_service.stream_token)
            response = self.client.post(
                f"/v1/booking-calls/voice-menu/{booking_service.stream_token}",
                data={"Digits": "3"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(websocket.receive_json()["status"], "declined")

    def test_direct_booking_call_does_not_require_cloud_itinerary_copy(self) -> None:
        payload = {
            "itinerary_id": "local-only-itinerary",
            "day_index": 2,
            "stop_index": 3,
            "confirmed": False,
            "details": {
                "venue_name": "Bakmi Nikmat Rasa",
                "venue_phone": "+6580241976",
                "reservation_datetime": "2026-07-05 13:00",
                "party_size": 2,
                "reservation_name": "Jason",
                "callback_phone": "+6512345678",
            },
        }

        rejected = self.client.post("/v1/booking-calls", json=payload)
        self.assertEqual(rejected.status_code, 400)

        payload["confirmed"] = True
        response = self.client.post("/v1/booking-calls", json=payload)

        self.assertEqual(response.status_code, 200)
        body = response.json()["call"]
        self.assertEqual(body["itinerary_id"], "local-only-itinerary")
        self.assertEqual(body["day_index"], 2)
        self.assertEqual(body["stop_index"], 3)
        self.assertEqual(body["venue_name"], "Bakmi Nikmat Rasa")
        self.assertEqual(body["status"], "fallback_required")
        self.assertIsNone(body["details"])

    def _configure_call_env(self) -> None:
        os.environ["TWILIO_ACCOUNT_SID"] = "AC123"
        os.environ["TWILIO_AUTH_TOKEN"] = "token"
        os.environ["TWILIO_FROM_NUMBER"] = "+15551230000"
        os.environ["PUBLIC_BACKEND_BASE_URL"] = "https://wanderlust.example"
        os.environ["GOOGLE_API_KEY"] = "test-google-key"
        get_settings.cache_clear()

    def test_activity_package_search_and_provider_checkout_are_guarded(self) -> None:
        self.preferences.create(
            "user-1",
            TravelPreferences(user_id="user-1", version=2, onboarding_required=False),
        )
        itinerary = self.client.post("/v1/itineraries", json=chat_itinerary_payload()).json()

        search = self.client.post(
            f"/v1/itineraries/{itinerary['id']}/commerce/packages/search",
            json={
                "itinerary_id": itinerary["id"],
                "day_index": 0,
                "stop_index": 0,
                "query": "family ticket",
            },
        )

        self.assertEqual(search.status_code, 200)
        result = search.json()
        self.assertEqual(result["activity_name"], "Colosseum")
        self.assertEqual(len(result["results"]), 5)
        self.assertTrue(result["results"][0]["checkout_url"].startswith("https://"))

        checkout_payload = {
            "package_id": result["results"][0]["package_id"],
            "checkout_url": result["results"][0]["checkout_url"],
            "confirmed": False,
        }
        rejected = self.client.post(
            f"/v1/itineraries/{itinerary['id']}/commerce/provider-checkout",
            json=checkout_payload,
        )
        self.assertEqual(rejected.status_code, 400)

        checkout_payload["confirmed"] = True
        checkout = self.client.post(
            f"/v1/itineraries/{itinerary['id']}/commerce/provider-checkout",
            json=checkout_payload,
        )
        self.assertEqual(checkout.status_code, 200)
        self.assertEqual(checkout.json()["checkout_url"], result["results"][0]["checkout_url"])

    def test_direct_route_compute_supports_local_only_itinerary_stops(self) -> None:
        with patch("app.api.routes.GoogleMapsClient", lambda: FakeRouteMapsClient()):
            response = self.client.post(
                "/v1/routes/compute",
                json={
                    "region": "Singapore",
                    "modes": ["WALKING"],
                    "stops": [
                        {"name": "Singapore Zoo"},
                        {"name": "River Wonders"},
                    ],
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["stop_coordinates"]), 2)
        self.assertEqual(len(body["segments"]), 1)
        self.assertEqual(body["segments"][0]["encoded_polyline"], "encoded-google-route")

    def test_direct_route_compute_selects_fastest_successful_mode_per_leg(self) -> None:
        with patch("app.api.routes.GoogleMapsClient", lambda: FakeMultiModeRouteMapsClient()):
            response = self.client.post(
                "/v1/routes/compute",
                json={
                    "region": "Singapore",
                    "modes": ["WALKING", "TRANSIT", "DRIVING"],
                    "stops": [
                        {"name": "Singapore Zoo"},
                        {"name": "River Wonders"},
                    ],
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["segments"]), 1)
        self.assertEqual(body["segments"][0]["mode"], "TRANSIT")
        self.assertEqual(body["segments"][0]["duration_seconds"], 420)
        self.assertEqual(body["segments"][0]["encoded_polyline"], "route-TRANSIT")

    def test_ask_anything_routes_to_call_or_package_ctas_without_side_effects(self) -> None:
        self.preferences.create(
            "user-1",
            TravelPreferences(user_id="user-1", version=2, onboarding_required=False),
        )
        itinerary = self.client.post("/v1/itineraries", json=chat_itinerary_payload()).json()

        booking = self.client.post(
            f"/v1/itineraries/{itinerary['id']}/ask-anything",
            json={"message": "Can you call to book this?", "day_index": 0, "stop_index": 0},
        )
        self.assertEqual(booking.status_code, 200)
        self.assertEqual(booking.json()["intent"], "booking")
        self.assertEqual(booking.json()["suggested_destination"], "call_venue")

        purchase = self.client.post(
            f"/v1/itineraries/{itinerary['id']}/ask-anything",
            json={"message": "Can I buy tickets?", "day_index": 0, "stop_index": 0},
        )
        self.assertEqual(purchase.status_code, 200)
        self.assertEqual(purchase.json()["intent"], "purchase")
        self.assertEqual(purchase.json()["suggested_destination"], "book_or_buy_packages")
        self.assertEqual(len(self.itineraries.items), 1)
        self.assertEqual(len(self.recovery_proposals.items), 0)

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
        itinerary = self.client.post("/v1/itineraries", json=chat_itinerary_payload()).json()

        self.assertEqual(
            self.client.post(f"/v1/itineraries/{itinerary['id']}/start").status_code, 200
        )
        self.assertEqual(
            self.client.post(f"/v1/itineraries/{itinerary['id']}/stop").status_code, 200
        )
        self.assertEqual(
            self.client.post(f"/v1/itineraries/{itinerary['id']}/complete").status_code,
            200,
        )

        pattern_response = self.client.post(f"/v1/itineraries/{itinerary['id']}/preference-pattern")
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
        itinerary = self.client.post("/v1/itineraries", json=chat_itinerary_payload()).json()
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
        stored = self.itineraries.get(itinerary["id"])
        self.assertEqual(stored.days[0].stops[0].name if stored else None, "Colosseum")

    def test_recovery_proposal_is_only_created_for_deviation_and_can_be_rejected(self) -> None:
        self.preferences.create(
            "user-1",
            TravelPreferences(user_id="user-1", version=4, onboarding_required=False),
        )
        itinerary = self.client.post("/v1/itineraries", json=itinerary_payload()).json()
        self.client.post(f"/v1/itineraries/{itinerary['id']}/start")

        normal_event = self.client.post(
            f"/v1/itineraries/{itinerary['id']}/location-events",
            json=location_event_payload(deviation_detected=False),
        )
        self.assertEqual(normal_event.status_code, 200)
        self.assertIsNone(normal_event.json()["recovery_proposal"])
        self.assertEqual(len(self.recovery_proposals.items), 0)

        deviated = self.client.post(
            f"/v1/itineraries/{itinerary['id']}/location-events",
            json=location_event_payload(deviation_detected=True),
        )
        proposal_id = deviated.json()["recovery_proposal"]["id"]
        reject_response = self.client.post(
            f"/v1/itineraries/{itinerary['id']}/recovery-proposals/{proposal_id}/reject"
        )
        self.assertEqual(reject_response.status_code, 200)
        self.assertEqual(reject_response.json()["status"], "REJECTED")


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


def chat_itinerary_payload() -> dict[str, object]:
    payload = itinerary_payload("Rome Chat Trip")
    payload["brief"] = {
        "region": "Rome",
        "description": "Historic sites and food",
        "trip_length_days": 1,
    }
    payload["days"] = [
        {
            "day_number": 1,
            "start_location": "Hotel",
            "end_location": "Hotel",
            "start_time": "09:00",
            "end_time": "18:00",
            "stops": [
                {
                    "id": "stop-1",
                    "name": "Colosseum",
                    "suggested_order": 1,
                    "time_window": "10:00 AM",
                    "what_to_do": "Explore the arena.",
                }
            ],
        }
    ]
    return payload


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


class FakeBookingMapsClient:
    def find_phone_number(self, query: str, *, region: str = "") -> str | None:
        return "+15551234567"


class FakeApiBookingCallService(BookingCallService):
    def __init__(self) -> None:
        super().__init__(maps_client=FakeBookingMapsClient())  # type: ignore[arg-type]
        self.stream_token: str | None = None

    def _create_twilio_call(self, *, to_number: str, stream_token: str) -> str:
        self.stream_token = stream_token
        return "CA1234567890"


class FakeRouteMapsClient:
    def geocode(self, query: str) -> Coordinates | None:
        if "Singapore Zoo" in query:
            return Coordinates(latitude=1.4043, longitude=103.7930)
        return Coordinates(latitude=1.4049, longitude=103.7907)

    def compute_route(
        self,
        *,
        origin: Coordinates,
        destination: Coordinates,
        travel_mode: str = "WALK",
    ) -> dict[str, object]:
        return {
            "routes": [
                {
                    "duration": "540s",
                    "distanceMeters": 900,
                    "polyline": {"encodedPolyline": "encoded-google-route"},
                }
            ]
        }


class FakeMultiModeRouteMapsClient(FakeRouteMapsClient):
    def compute_route(
        self,
        *,
        origin: Coordinates,
        destination: Coordinates,
        travel_mode: str = "WALK",
    ) -> dict[str, object]:
        if travel_mode == "DRIVING":
            raise RuntimeError("Driving route unavailable")
        durations = {"WALKING": 900, "TRANSIT": 420}
        duration = durations[travel_mode]
        return {
            "routes": [
                {
                    "duration": f"{duration}s",
                    "distanceMeters": 900,
                    "polyline": {"encodedPolyline": f"route-{travel_mode}"},
                }
            ]
        }


class FakeTimingChatAgent:
    def process_message(self, **kwargs) -> dict[str, object]:
        return {
            "agent_message": "Moved the first stop later.",
            "action": "update_timing",
            "timing_update": {"target_stop_index": 0, "time_window": "11:30 AM"},
        }


class FakeRewriteChatAgent:
    def process_message(self, **kwargs) -> dict[str, object]:
        itinerary = kwargs["itinerary"].model_copy(deep=True)
        itinerary.title = "Proposed rewrite"
        return {
            "agent_message": "Review this rewrite.",
            "action": "propose_rewrite",
            "proposal": {
                "title": "Proposed rewrite",
                "summary": "A safer preview only.",
                "proposed_itinerary": itinerary,
            },
        }


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
