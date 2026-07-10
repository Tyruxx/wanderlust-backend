from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Generic, TypeVar

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

_DB_PATH = (
    ":memory:"
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("WANDERLUST_DB") == "memory"
    else str(Path(__file__).resolve().parent.parent.parent / "wanderlust.db")
)
_local = threading.local()


def _get_connection() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    return conn


class _SerializationError(RuntimeError):
    pass


class SqliteRepository(Generic[T]):
    def __init__(self, table_name: str, model_cls: type[T]) -> None:
        settings = get_settings()
        if settings.wanderlust_storage_backend.strip().lower() == "firestore":
            self._delegate: FirestoreRepository[T] | None = FirestoreRepository(
                table_name,
                model_cls,
                collection_prefix=settings.firestore_collection_prefix,
            )
            return
        self._delegate = None
        self._table = f"repo_{table_name}"
        self._model_cls = model_cls
        self._init_table()

    def _init_table(self) -> None:
        conn = _get_connection()
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {self._table} (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
        )
        conn.commit()

    def _to_row(self, model: T) -> str:
        return json.dumps(model.model_dump(mode="json"), default=str)

    def _from_row(self, doc_id: str, raw: str) -> T:
        data = json.loads(raw)
        data["id"] = doc_id
        try:
            return self._model_cls.model_validate(data)
        except Exception as exc:
            raise _SerializationError(
                f"Failed to deserialize {self._model_cls.__name__}"
            ) from exc

    def create(self, doc_id: str, model: T) -> T:
        if self._delegate is not None:
            return self._delegate.create(doc_id, model)
        conn = _get_connection()
        conn.execute(
            f"INSERT OR REPLACE INTO {self._table} (id, data) VALUES (?, ?)",
            (doc_id, self._to_row(model)),
        )
        conn.commit()
        return model

    def get(self, doc_id: str) -> T | None:
        if self._delegate is not None:
            return self._delegate.get(doc_id)
        conn = _get_connection()
        row = conn.execute(
            f"SELECT data FROM {self._table} WHERE id = ?", (doc_id,)
        ).fetchone()
        if row is None:
            return None
        return self._from_row(doc_id, row["data"])

    def update(self, doc_id: str, model: T) -> T:
        if self._delegate is not None:
            return self._delegate.update(doc_id, model)
        return self.create(doc_id, model)

    def delete(self, doc_id: str) -> None:
        if self._delegate is not None:
            self._delegate.delete(doc_id)
            return
        conn = _get_connection()
        conn.execute(f"DELETE FROM {self._table} WHERE id = ?", (doc_id,))
        conn.commit()

    def query_by_field(self, field: str, value: object) -> list[T]:
        if self._delegate is not None:
            return self._delegate.query_by_field(field, value)
        conn = _get_connection()
        rows = conn.execute(f"SELECT id, data FROM {self._table}").fetchall()
        results: list[T] = []
        for row in rows:
            data = json.loads(row["data"])
            if data.get(field) == value:
                results.append(self._from_row(row["id"], row["data"]))
        return results

    def list_all(self) -> list[T]:
        if self._delegate is not None:
            return self._delegate.list_all()
        conn = _get_connection()
        rows = conn.execute(f"SELECT id, data FROM {self._table}").fetchall()
        return [self._from_row(row["id"], row["data"]) for row in rows]

    def clear(self) -> None:
        if self._delegate is not None:
            self._delegate.clear()
            return
        conn = _get_connection()
        conn.execute(f"DELETE FROM {self._table}")
        conn.commit()


class FirestoreRepository(Generic[T]):
    def __init__(
        self,
        table_name: str,
        model_cls: type[T],
        *,
        collection_prefix: str = "wanderlust",
        client: object | None = None,
    ) -> None:
        safe_prefix = collection_prefix.strip() or "wanderlust"
        self._collection_name = f"{safe_prefix}_{table_name}"
        self._model_cls = model_cls
        self._client = client

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from google.cloud import firestore
        except Exception as exc:  # pragma: no cover - optional cloud dependency.
            raise RuntimeError("google-cloud-firestore is not installed.") from exc
        self._client = firestore.Client()
        return self._client

    def _collection(self):
        return self._get_client().collection(self._collection_name)

    def _to_document(self, doc_id: str, model: T) -> dict[str, object]:
        data = model.model_dump(mode="json")
        data["id"] = doc_id
        return {"data": data}

    def _from_document(self, doc_id: str, raw: dict[str, object] | None) -> T | None:
        if not raw:
            return None
        data = raw.get("data")
        if not isinstance(data, dict):
            return None
        model_data = dict(data)
        model_data["id"] = doc_id
        try:
            return self._model_cls.model_validate(model_data)
        except Exception as exc:
            raise _SerializationError(
                f"Failed to deserialize {self._model_cls.__name__}"
            ) from exc

    def create(self, doc_id: str, model: T) -> T:
        self._collection().document(doc_id).set(self._to_document(doc_id, model))
        return model

    def get(self, doc_id: str) -> T | None:
        snapshot = self._collection().document(doc_id).get()
        if not getattr(snapshot, "exists", False):
            return None
        return self._from_document(doc_id, snapshot.to_dict())

    def update(self, doc_id: str, model: T) -> T:
        return self.create(doc_id, model)

    def delete(self, doc_id: str) -> None:
        self._collection().document(doc_id).delete()

    def query_by_field(self, field: str, value: object) -> list[T]:
        return [
            item
            for item in self.list_all()
            if getattr(item, field, None) == value
        ]

    def list_all(self) -> list[T]:
        results: list[T] = []
        for snapshot in self._collection().stream():
            item = self._from_document(snapshot.id, snapshot.to_dict())
            if item is not None:
                results.append(item)
        return results

    def clear(self) -> None:
        for snapshot in self._collection().stream():
            snapshot.reference.delete()


class AuditLogEntry(BaseModel):
    event_id: str
    user_id: str
    action: str
    target_type: str
    target_id: str
    timestamp: str = ""
    details: str = ""


class TravelPreferencesRepository(SqliteRepository[TravelPreferences]):
    def __init__(self) -> None:
        super().__init__("preferences", TravelPreferences)

    def get_by_user(self, user_id: str) -> TravelPreferences | None:
        direct = self.get(user_id)
        if direct:
            return direct
        results = self.query_by_field("user_id", user_id)
        return results[0] if results else None


class ItineraryRepository(SqliteRepository[Itinerary]):
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


class DynamicPreferencesRepository(SqliteRepository[DynamicBehaviorPreferences]):
    def __init__(self) -> None:
        super().__init__("dynamic_preferences", DynamicBehaviorPreferences)

    def get_by_itinerary(self, itinerary_id: str) -> DynamicBehaviorPreferences | None:
        return self.get(itinerary_id)


class EvidenceRepository(SqliteRepository[SourceEvidence]):
    def __init__(self) -> None:
        super().__init__("evidence", SourceEvidence)


class RecommendationRepository(SqliteRepository[Recommendation]):
    def __init__(self) -> None:
        super().__init__("recommendations", Recommendation)


class RecoveryProposalRepository(SqliteRepository[RecoveryProposal]):
    def __init__(self) -> None:
        super().__init__("recovery_proposals", RecoveryProposal)

    def find_pending_by_itinerary(self, itinerary_id: str) -> list[RecoveryProposal]:
        return [
            proposal
            for proposal in self.query_by_field("itinerary_id", itinerary_id)
            if proposal.status == RecoveryProposalStatus.PENDING
        ]


class AuditLogRepository(SqliteRepository[AuditLogEntry]):
    def __init__(self) -> None:
        super().__init__("audit_logs", AuditLogEntry)
