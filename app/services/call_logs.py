from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Protocol

from pydantic import BaseModel, Field

from app.core.settings import get_settings

logger = logging.getLogger(__name__)


class CallLogEntry(BaseModel):
    call_id: str
    itinerary_id: str
    user_hash: str
    day_index: int = Field(ge=0)
    stop_index: int = Field(ge=0)
    venue_name: str
    status: str
    provider_call_sid: str | None = None
    result_summary: str | None = None
    occurred_at: str


class CallLogRepository(Protocol):
    def write(self, entry: CallLogEntry) -> None: ...


class DisabledCallLogRepository:
    def write(self, entry: CallLogEntry) -> None:
        _ = entry


class MemoryCallLogRepository:
    def __init__(self) -> None:
        self.entries: list[CallLogEntry] = []

    def write(self, entry: CallLogEntry) -> None:
        self.entries.append(entry)


class FirestoreCallLogRepository:
    def __init__(self, *, collection_name: str) -> None:
        self.collection_name = collection_name
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from google.cloud import firestore
        except Exception as exc:  # pragma: no cover - optional cloud dependency.
            raise RuntimeError("google-cloud-firestore is not installed.") from exc
        self._client = firestore.Client()
        return self._client

    def write(self, entry: CallLogEntry) -> None:
        client = self._get_client()
        client.collection(self.collection_name).document(entry.call_id).collection("events").add(
            entry.model_dump(mode="json")
        )


def build_call_log_repository() -> CallLogRepository:
    settings = get_settings()
    backend = settings.call_log_backend.strip().lower()
    if backend == "memory":
        return MemoryCallLogRepository()
    if backend == "firestore":
        return FirestoreCallLogRepository(collection_name=settings.call_log_collection)
    return DisabledCallLogRepository()


def redact_user_id(user_id: str) -> str:
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_write_call_log(repository: CallLogRepository, entry: CallLogEntry) -> None:
    try:
        repository.write(entry)
    except Exception as exc:
        logger.warning("call log write failed call_id=%s: %s", entry.call_id, exc)
