from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

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


class _SerializationError(RuntimeError):
    pass


class LocalRepository(Generic[T]):
    def __init__(self, model_cls: type[T]) -> None:
        self._store: dict[str, T] = {}
        self._model_cls = model_cls

    def create(self, doc_id: str, model: T) -> T:
        self._store[doc_id] = model
        return model

    def get(self, doc_id: str) -> T | None:
        return self._store.get(doc_id)

    def update(self, doc_id: str, model: T) -> T:
        self._store[doc_id] = model
        return model

    def delete(self, doc_id: str) -> None:
        self._store.pop(doc_id, None)

    def query_by_field(self, field: str, value: object) -> list[T]:
        return [
            model
            for model in self._store.values()
            if getattr(model, field, None) == value
        ]

    def list_all(self) -> list[T]:
        return list(self._store.values())


class AuditLogEntry(BaseModel):
    event_id: str
    user_id: str
    action: str
    target_type: str
    target_id: str
    timestamp: str = ""
    details: str = ""


class TravelPreferencesRepository(LocalRepository[TravelPreferences]):
    def __init__(self) -> None:
        super().__init__(TravelPreferences)

    def get_by_user(self, user_id: str) -> TravelPreferences | None:
        direct = self.get(user_id)
        if direct:
            return direct
        results = self.query_by_field("user_id", user_id)
        return results[0] if results else None


class ItineraryRepository(LocalRepository[Itinerary]):
    def __init__(self) -> None:
        super().__init__(Itinerary)

    def find_by_user(self, user_id: str) -> list[Itinerary]:
        return self.query_by_field("user_id", user_id)

    def find_active(self, user_id: str) -> Itinerary | None:
        results = self.query_by_field("user_id", user_id)
        for itinerary in results:
            if itinerary.status == ItineraryStatus.ACTIVE:
                return itinerary
        return None


class DynamicPreferencesRepository(LocalRepository[DynamicBehaviorPreferences]):
    def __init__(self) -> None:
        super().__init__(DynamicBehaviorPreferences)

    def get_by_itinerary(self, itinerary_id: str) -> DynamicBehaviorPreferences | None:
        return self.get(itinerary_id)


class EvidenceRepository(LocalRepository[SourceEvidence]):
    def __init__(self) -> None:
        super().__init__(SourceEvidence)


class RecommendationRepository(LocalRepository[Recommendation]):
    def __init__(self) -> None:
        super().__init__(Recommendation)


class RecoveryProposalRepository(LocalRepository[RecoveryProposal]):
    def __init__(self) -> None:
        super().__init__(RecoveryProposal)

    def find_pending_by_itinerary(self, itinerary_id: str) -> list[RecoveryProposal]:
        return [
            proposal
            for proposal in self.query_by_field("itinerary_id", itinerary_id)
            if proposal.status == RecoveryProposalStatus.PENDING
        ]


class AuditLogRepository(LocalRepository[AuditLogEntry]):
    def __init__(self) -> None:
        super().__init__(AuditLogEntry)
