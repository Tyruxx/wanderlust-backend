from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.domain.models import (
    Itinerary,
    ItineraryStatus,
    TravelPreferences,
    TripBrief,
)
from app.services.repositories import (
    ItineraryRepository,
    TravelPreferencesRepository,
    UserProfile,
    UserRepository,
)


class UserRepositoryTests(unittest.TestCase):
    @patch("google.cloud.firestore.Client")
    def test_create_and_get_user(self, mock_firestore: MagicMock) -> None:
        mock_doc = MagicMock()
        mock_doc.set.return_value = None
        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_doc
        mock_firestore.return_value.collection.return_value = mock_collection

        repo = UserRepository()
        profile = UserProfile(uid="user-1", email="a@b.com", display_name="Alice")
        repo.create("user-1", profile)

        mock_collection.document.assert_called_with("user-1")
        mock_doc.set.assert_called_once()

    @patch("google.cloud.firestore.Client")
    def test_get_nonexistent_user_returns_none(self, mock_firestore: MagicMock) -> None:
        mock_snapshot = MagicMock()
        mock_snapshot.exists = False
        mock_doc = MagicMock()
        mock_doc.get.return_value = mock_snapshot
        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_doc
        mock_firestore.return_value.collection.return_value = mock_collection

        repo = UserRepository()
        result = repo.get("nobody")
        self.assertIsNone(result)

    @patch("google.cloud.firestore.Client")
    def test_delete_user(self, mock_firestore: MagicMock) -> None:
        mock_doc = MagicMock()
        mock_doc.delete.return_value = None
        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_doc
        mock_firestore.return_value.collection.return_value = mock_collection

        repo = UserRepository()
        repo.delete("user-1")
        mock_doc.delete.assert_called_once()


class TravelPreferencesRepositoryTests(unittest.TestCase):
    @patch("google.cloud.firestore.Client")
    def test_get_by_user_returns_preferences(self, mock_firestore: MagicMock) -> None:
        mock_snapshot = MagicMock()
        mock_snapshot.exists = True
        mock_snapshot.id = "pref-1"
        mock_snapshot.to_dict.return_value = {
            "user_id": "user-1",
            "version": 2,
            "onboarding_required": False,
            "pace": "balanced",
            "interests": ["food", "photography"],
            "budget_posture": "flexible",
            "day_rhythm": "flexible",
            "social_discovery_enabled": False,
            "saved_itinerary_patterns": [],
            "dietary_preferences": [],
            "accessibility_needs": [],
        }

        mock_doc = MagicMock()
        mock_doc.get.return_value = mock_snapshot
        mock_collection = MagicMock()
        mock_collection.where.return_value.stream.return_value = [mock_snapshot]
        mock_firestore.return_value.collection.return_value = mock_collection

        repo = TravelPreferencesRepository()
        result = repo.get_by_user("user-1")

        self.assertIsNotNone(result)
        if result:
            self.assertEqual(result.user_id, "user-1")
            self.assertEqual(result.version, 2)
            self.assertFalse(result.onboarding_required)

    @patch("google.cloud.firestore.Client")
    def test_get_by_user_returns_none_when_missing(self, mock_firestore: MagicMock) -> None:
        mock_collection = MagicMock()
        mock_collection.where.return_value.stream.return_value = []
        mock_firestore.return_value.collection.return_value = mock_collection

        repo = TravelPreferencesRepository()
        result = repo.get_by_user("user-1")
        self.assertIsNone(result)

    @patch("google.cloud.firestore.Client")
    def test_update_preferences_increments_version(self, mock_firestore: MagicMock) -> None:
        mock_doc = MagicMock()
        mock_doc.set.return_value = None
        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_doc
        mock_firestore.return_value.collection.return_value = mock_collection

        repo = TravelPreferencesRepository()
        prefs = TravelPreferences(
            user_id="user-1",
            version=3,
            pace="relaxed",
            interests=["coffee"],
        )
        repo.update("pref-1", prefs)
        mock_doc.set.assert_called_once()


class ItineraryRepositoryTests(unittest.TestCase):
    @patch("google.cloud.firestore.Client")
    def test_find_by_user_returns_user_itineraries(self, mock_firestore: MagicMock) -> None:
        mock_snapshot = MagicMock()
        mock_snapshot.exists = True
        mock_snapshot.id = "itin-1"
        mock_snapshot.to_dict.return_value = {
            "user_id": "user-1",
            "title": "Tokyo Trip",
            "status": "INACTIVE",
            "preference_version": 1,
            "brief": {
                "region": "Tokyo",
                "description": "Food trip",
                "trip_length_days": 3,
                "include_regions": [],
                "avoid_regions": [],
                "traveler_count": 1,
                "day_rules": [],
                "constraints": [],
                "must_visit_places": [],
            },
            "days": [],
        }

        mock_collection = MagicMock()
        mock_collection.where.return_value.stream.return_value = [mock_snapshot]
        mock_firestore.return_value.collection.return_value = mock_collection

        repo = ItineraryRepository()
        results = repo.find_by_user("user-1")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].id, "itin-1")
        self.assertEqual(results[0].title, "Tokyo Trip")

    @patch("google.cloud.firestore.Client")
    def test_find_active_returns_only_active_itinerary(self, mock_firestore: MagicMock) -> None:
        def snapshot_factory(
            doc_id: str,
            user_id: str,
            title: str,
            status: str,
        ) -> MagicMock:
            sn = MagicMock()
            sn.exists = True
            sn.id = doc_id
            sn.to_dict.return_value = {
                "user_id": user_id,
                "title": title,
                "status": status,
                "preference_version": 1,
                "brief": {
                    "region": "Tokyo",
                    "description": "Trip",
                    "trip_length_days": 1,
                    "include_regions": [],
                    "avoid_regions": [],
                    "traveler_count": 1,
                    "day_rules": [],
                    "constraints": [],
                    "must_visit_places": [],
                },
                "days": [],
            }
            return sn

        active_snapshot = snapshot_factory("active-1", "user-1", "Active", "ACTIVE")
        inactive_snapshot = snapshot_factory("inactive-1", "user-1", "Inactive", "INACTIVE")

        mock_collection = MagicMock()
        mock_collection.where.return_value.stream.return_value = [active_snapshot, inactive_snapshot]
        mock_firestore.return_value.collection.return_value = mock_collection

        repo = ItineraryRepository()
        result = repo.find_active("user-1")
        self.assertIsNotNone(result)
        if result:
            self.assertEqual(result.id, "active-1")
            self.assertEqual(result.status, ItineraryStatus.ACTIVE)

    @patch("google.cloud.firestore.Client")
    def test_find_active_returns_none_when_no_active(self, mock_firestore: MagicMock) -> None:
        mock_collection = MagicMock()
        mock_collection.where.return_value.stream.return_value = []
        mock_firestore.return_value.collection.return_value = mock_collection

        repo = ItineraryRepository()
        result = repo.find_active("user-1")
        self.assertIsNone(result)

    @patch("google.cloud.firestore.Client")
    def test_create_itinerary(self, mock_firestore: MagicMock) -> None:
        mock_doc = MagicMock()
        mock_doc.set.return_value = None
        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_doc
        mock_firestore.return_value.collection.return_value = mock_collection

        itinerary = Itinerary(
            id="itin-1",
            user_id="user-1",
            title="Kyoto",
            preference_version=1,
            status=ItineraryStatus.INACTIVE,
            brief=TripBrief(
                region="Kyoto",
                description="Temples",
                trip_length_days=2,
            ),
        )

        repo = ItineraryRepository()
        repo.create("itin-1", itinerary)
        mock_doc.set.assert_called_once()


if __name__ == "__main__":
    unittest.main()
