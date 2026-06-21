from __future__ import annotations

import unittest

from app.domain.models import (
    Itinerary,
    ItineraryStatus,
    TravelPreferences,
    TripBrief,
)
from app.services.repositories import (
    ItineraryRepository,
    TravelPreferencesRepository,
)


class TravelPreferencesRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = TravelPreferencesRepository()
        self.repo.clear()

    def test_get_by_user_returns_preferences(self) -> None:
        prefs = TravelPreferences(
            user_id="user-1",
            version=2,
            onboarding_required=False,
            pace="balanced",
            interests=["food", "photography"],
        )
        self.repo.create("user-1", prefs)

        result = self.repo.get_by_user("user-1")
        self.assertIsNotNone(result)
        if result:
            self.assertEqual(result.user_id, "user-1")
            self.assertEqual(result.version, 2)
            self.assertFalse(result.onboarding_required)

    def test_get_by_user_returns_none_when_missing(self) -> None:
        result = self.repo.get_by_user("user-1")
        self.assertIsNone(result)

    def test_update_preferences_increments_version(self) -> None:
        prefs = TravelPreferences(
            user_id="user-1",
            version=3,
            pace="relaxed",
            interests=["coffee"],
        )
        self.repo.create("user-1", prefs)

        updated = prefs.model_copy(update={"version": 4})
        self.repo.update("user-1", updated)

        result = self.repo.get_by_user("user-1")
        self.assertIsNotNone(result)
        if result:
            self.assertEqual(result.version, 4)


class ItineraryRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = ItineraryRepository()
        self.repo.clear()

    def _make_itinerary(
        self,
        doc_id: str,
        user_id: str = "user-1",
        title: str = "Trip",
        status: ItineraryStatus = ItineraryStatus.INACTIVE,
    ) -> Itinerary:
        return Itinerary(
            id=doc_id,
            user_id=user_id,
            title=title,
            preference_version=1,
            status=status,
            brief=TripBrief(
                region="Tokyo",
                description="Trip",
                trip_length_days=1,
            ),
        )

    def test_find_by_user_returns_user_itineraries(self) -> None:
        self.repo.create("itin-1", self._make_itinerary("itin-1", title="Tokyo Trip"))

        results = self.repo.find_by_user("user-1")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].id, "itin-1")
        self.assertEqual(results[0].title, "Tokyo Trip")

    def test_find_by_user_returns_empty_when_no_match(self) -> None:
        self.repo.create("itin-1", self._make_itinerary("itin-1", user_id="other-user"))

        results = self.repo.find_by_user("user-1")
        self.assertEqual(len(results), 0)

    def test_find_active_returns_only_active_itinerary(self) -> None:
        self.repo.create("active-1", self._make_itinerary("active-1", title="Active", status=ItineraryStatus.ACTIVE))
        self.repo.create("inactive-1", self._make_itinerary("inactive-1", title="Inactive", status=ItineraryStatus.INACTIVE))

        result = self.repo.find_active("user-1")
        self.assertIsNotNone(result)
        if result:
            self.assertEqual(result.id, "active-1")
            self.assertEqual(result.status, ItineraryStatus.ACTIVE)

    def test_find_active_returns_none_when_no_active(self) -> None:
        self.repo.create("inactive-1", self._make_itinerary("inactive-1", title="Inactive"))

        result = self.repo.find_active("user-1")
        self.assertIsNone(result)

    def test_create_itinerary(self) -> None:
        itinerary = self._make_itinerary("itin-1", title="Kyoto")
        self.repo.create("itin-1", itinerary)

        result = self.repo.get("itin-1")
        self.assertIsNotNone(result)
        if result:
            self.assertEqual(result.title, "Kyoto")

    def test_delete_removes_itinerary(self) -> None:
        self.repo.create("itin-1", self._make_itinerary("itin-1"))
        self.repo.delete("itin-1")

        result = self.repo.get("itin-1")
        self.assertIsNone(result)

    def test_list_all_returns_all(self) -> None:
        self.repo.create("a", self._make_itinerary("a"))
        self.repo.create("b", self._make_itinerary("b"))

        all_items = self.repo.list_all()
        self.assertEqual(len(all_items), 2)


if __name__ == "__main__":
    unittest.main()
