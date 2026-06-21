from __future__ import annotations

from typing import Generic, TypeVar

from google.cloud import firestore
from pydantic import BaseModel

from app.core.settings import get_settings
from app.domain.models import (
    DynamicBehaviorPreferences,
    Itinerary,
    ItineraryStatus,
    Recommendation,
    RecoveryProposal,
    RecoveryProposalStatus,
    SourceEvidence,
    TravelPreferences,
)

T = TypeVar("T", bound=BaseModel)

COLLECTION_PREFIX = "wanderlust"


class _SerializationError(RuntimeError):
    pass


class FirestoreRepository(Generic[T]):
    def __init__(self, collection_name: str, model_cls: type[T]) -> None:
        settings = get_settings()
        if settings.app_env == "test":
            self._db: firestore.Client | None = None
        else:
            self._db = firestore.Client(
                project=settings.google_cloud_project,
                database=settings.firestore_database_id,
            )
        self._collection_ref = COLLECTION_PREFIX + "__" + collection_name
        self._model_cls = model_cls

    @property
    def _col(self) -> firestore.CollectionReference:
        if self._db is None:
            raise RuntimeError("Firestore client not available in test environment")
        return self._db.collection(self._collection_ref)

    def _doc_ref(self, doc_id: str) -> firestore.DocumentReference:
        return self._col.document(doc_id)

    def create(self, doc_id: str, model: T) -> T:
        self._doc_ref(doc_id).set(self._to_dict(model))
        return model

    def get(self, doc_id: str) -> T | None:
        snapshot = self._doc_ref(doc_id).get()
        if snapshot.exists is not True:
            return None
        data = snapshot.to_dict()
        if data is None:
            return None
        return self._from_dict(data, doc_id)

    def update(self, doc_id: str, model: T) -> T:
        self._doc_ref(doc_id).set(self._to_dict(model))
        return model

    def delete(self, doc_id: str) -> None:
        self._doc_ref(doc_id).delete()

    def query_by_field(self, field: str, value: object) -> list[T]:
        docs = self._col.where(field, "==", value).stream()
        return [self._from_dict(doc.to_dict(), doc.id) for doc in docs]

    def list_all(self) -> list[T]:
        docs = self._col.stream()
        return [self._from_dict(doc.to_dict(), doc.id) for doc in docs]

    def _to_dict(self, model: T) -> dict[str, object]:
        raw: dict[str, object] = model.model_dump(mode="json", exclude={"id"})
        return _normalize_firestore(raw)

    def _from_dict(self, data: dict[str, object], doc_id: str) -> T:
        data = dict(data)
        data["id"] = doc_id
        try:
            return self._model_cls.model_validate(data)
        except Exception as exc:
            raise _SerializationError(f"Failed to deserialize {self._model_cls.__name__}") from exc


class UserProfile(BaseModel):
    uid: str
    email: str = ""
    display_name: str = ""
    photo_url: str = ""
    created_at: str = ""
    auth_provider: str = "google.com"


class AuditLogEntry(BaseModel):
    event_id: str
    user_id: str
    action: str
    target_type: str
    target_id: str
    timestamp: str = ""
    details: str = ""


class UserRepository(FirestoreRepository[UserProfile]):
    def __init__(self) -> None:
        super().__init__("users", UserProfile)


class TravelPreferencesRepository(FirestoreRepository[TravelPreferences]):
    def __init__(self) -> None:
        super().__init__("preferences", TravelPreferences)

    def get_by_user(self, user_id: str) -> TravelPreferences | None:
        direct = self.get(user_id)
        if direct:
            return direct
        results = self.query_by_field("user_id", user_id)
        return results[0] if results else None


class ItineraryRepository(FirestoreRepository[Itinerary]):
    def __init__(self) -> None:
        super().__init__("itineraries", Itinerary)

    def find_by_user(self, user_id: str) -> list[Itinerary]:
        return self.query_by_field("user_id", user_id)

    def find_active(self, user_id: str) -> Itinerary | None:
        results = self.query_by_field("user_id", user_id)
        for itinerary in results:
            if itinerary.status == ItineraryStatus.ACTIVE:
                return itinerary
        return None


class DynamicPreferencesRepository(FirestoreRepository[DynamicBehaviorPreferences]):
    def __init__(self) -> None:
        super().__init__("dynamic_preferences", DynamicBehaviorPreferences)

    def get_by_itinerary(self, itinerary_id: str) -> DynamicBehaviorPreferences | None:
        return self.get(itinerary_id)


class EvidenceRepository(FirestoreRepository[SourceEvidence]):
    def __init__(self) -> None:
        super().__init__("evidence", SourceEvidence)


class RecommendationRepository(FirestoreRepository[Recommendation]):
    def __init__(self) -> None:
        super().__init__("recommendations", Recommendation)


class RecoveryProposalRepository(FirestoreRepository[RecoveryProposal]):
    def __init__(self) -> None:
        super().__init__("recovery_proposals", RecoveryProposal)

    def find_pending_by_itinerary(self, itinerary_id: str) -> list[RecoveryProposal]:
        return [
            proposal
            for proposal in self.query_by_field("itinerary_id", itinerary_id)
            if proposal.status == RecoveryProposalStatus.PENDING
        ]


class AuditLogRepository(FirestoreRepository[AuditLogEntry]):
    def __init__(self) -> None:
        super().__init__("audit_logs", AuditLogEntry)


def _normalize_firestore(data: dict[str, object]) -> dict[str, object]:
    cleaned: dict[str, object] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            cleaned[key] = _normalize_firestore(value)
        elif isinstance(value, list):
            cleaned[key] = [_normalize_firestore(v) if isinstance(v, dict) else v for v in value]
        elif isinstance(value, BaseModel):
            cleaned[key] = _normalize_firestore(value.model_dump(mode="python"))
        else:
            cleaned[key] = value
    return cleaned
