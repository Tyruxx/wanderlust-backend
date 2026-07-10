from __future__ import annotations

import unittest

from app.domain.models import (
    Itinerary,
    ItineraryStatus,
    TravelPreferences,
    TripBrief,
)
from app.services.repositories import (
    FirestoreRepository,
    ItineraryRepository,
    TravelPreferencesRepository,
)


class FakeSnapshot:
    def __init__(self, doc_id: str, data: dict[str, object] | None, collection: "FakeCollection") -> None:
        self.id = doc_id
        self._data = data
        self.exists = data is not None
        self.reference = FakeDocument(doc_id, collection)

    def to_dict(self) -> dict[str, object] | None:
        return self._data


class FakeDocument:
    def __init__(self, doc_id: str, collection: "FakeCollection") -> None:
        self._doc_id = doc_id
        self._collection = collection

    def set(self, data: dict[str, object]) -> None:
        self._collection.data[self._doc_id] = data

    def get(self) -> FakeSnapshot:
        return FakeSnapshot(
            self._doc_id,
            self._collection.data.get(self._doc_id),
            self._collection,
        )

    def delete(self) -> None:
        self._collection.data.pop(self._doc_id, None)


class FakeCollection:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, object]] = {}

    def document(self, doc_id: str) -> FakeDocument:
        return FakeDocument(doc_id, self)

    def stream(self) -> list[FakeSnapshot]:
        return [
            FakeSnapshot(doc_id, data, self)
            for doc_id, data in self.data.items()
        ]


class FakeFirestoreClient:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def collection(self, name: str) -> FakeCollection:
        self.collections.setdefault(name, FakeCollection())
        return self.collections[name]


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


class FirestoreRepositoryTests(unittest.TestCase):
    def test_firestore_preferences_are_stored_in_prefixed_collection(self) -> None:
        client = FakeFirestoreClient()
        repo = FirestoreRepository(
            "preferences",
            TravelPreferences,
            collection_prefix="wanderlust_test",
            client=client,
        )
        prefs = TravelPreferences(
            user_id="anon-device-1",
            version=2,
            onboarding_required=False,
            pace="balanced",
        )

        repo.create("anon-device-1", prefs)

        self.assertIn("wanderlust_test_preferences", client.collections)
        stored = repo.get("anon-device-1")
        self.assertIsNotNone(stored)
        if stored:
            self.assertEqual(stored.user_id, "anon-device-1")
            self.assertFalse(stored.onboarding_required)
        self.assertEqual(len(repo.query_by_field("user_id", "anon-device-1")), 1)


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
