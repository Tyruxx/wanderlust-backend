from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from google.cloud import pubsub_v1

from app.core.settings import get_settings
from app.domain.models import (
    ActiveEventIngestionResult,
    AgentActionType,
    DynamicBehaviorPreferences,
    Itinerary,
    LocationEvent,
    RecoveryProposal,
    RecoveryProposalStatus,
    SourceConfidence,
)
from app.services.guardrails import ActionGuardrailService, ItineraryLifecycleService
from app.services.repositories import (
    DynamicPreferencesRepository,
    ItineraryRepository,
    RecoveryProposalRepository,
    TravelPreferencesRepository,
)


class PubSubPublishError(RuntimeError):
    pass


@dataclass(frozen=True)
class ActiveEventRepositoryBundle:
    preferences: TravelPreferencesRepository
    itineraries: ItineraryRepository
    dynamic_preferences: DynamicPreferencesRepository
    recovery_proposals: RecoveryProposalRepository


class PubSubLocationEventPublisher:
    def __init__(self) -> None:
        settings = get_settings()
        self.project_id = settings.google_cloud_project
        self.topic_name = settings.pubsub_location_events_topic
        self.publisher = pubsub_v1.PublisherClient()

    def publish(self, event: LocationEvent) -> str:
        if not self.project_id:
            raise PubSubPublishError("GOOGLE_CLOUD_PROJECT is required to publish Pub/Sub events.")
        topic_path = self.publisher.topic_path(self.project_id, self.topic_name)
        future = self.publisher.publish(
            topic_path,
            json.dumps(event.model_dump(mode="json")).encode("utf-8"),
            itinerary_id=event.itinerary_id,
            user_id=event.user_id,
        )
        return str(future.result())


class ActiveEventWorkflowService:
    def __init__(
        self,
        *,
        publisher: PubSubLocationEventPublisher | None = None,
    ) -> None:
        self.publisher = publisher or PubSubLocationEventPublisher()
        self.lifecycle = ItineraryLifecycleService()

    def ingest_location_event(
        self,
        *,
        event: LocationEvent,
        itinerary: Itinerary,
        repositories: ActiveEventRepositoryBundle,
    ) -> ActiveEventIngestionResult:
        self.lifecycle.assert_can_ingest_active_event(itinerary)
        preferences = repositories.preferences.get_by_user(event.user_id)
        dynamic_preferences = repositories.dynamic_preferences.get_by_itinerary(itinerary.id)
        if dynamic_preferences is None:
            dynamic_preferences = DynamicBehaviorPreferences(itinerary_id=itinerary.id)

        if preferences is not None:
            dynamic_preferences.version += 1
        dynamic_preferences.confidence = SourceConfidence.LOW
        repositories.dynamic_preferences.update(itinerary.id, dynamic_preferences)

        published_event_id = self.publisher.publish(event)
        proposal = self._maybe_create_recovery_proposal(
            event=event,
            itinerary=itinerary,
            preference_version=preferences.version if preferences else itinerary.preference_version,
            repositories=repositories,
        )
        return ActiveEventIngestionResult(
            accepted=True,
            itinerary_id=itinerary.id,
            published_event_id=published_event_id,
            dynamic_preference_version=dynamic_preferences.version,
            recovery_proposal=proposal,
        )

    def accept_recovery_proposal(
        self,
        *,
        proposal: RecoveryProposal,
        confirmed: bool,
        repositories: ActiveEventRepositoryBundle,
    ) -> RecoveryProposal:
        ActionGuardrailService().assert_explicit_confirmation(
            action=AgentActionType.APPLY_RECOVERY,
            confirmed=confirmed,
        )
        if proposal.status != RecoveryProposalStatus.PENDING:
            return proposal
        accepted = proposal.model_copy(
            update={
                "status": RecoveryProposalStatus.ACCEPTED,
                "decided_at": _utc_timestamp(),
            },
            deep=True,
        )
        repositories.recovery_proposals.update(accepted.id, accepted)
        return accepted

    def reject_recovery_proposal(
        self,
        *,
        proposal: RecoveryProposal,
        repositories: ActiveEventRepositoryBundle,
    ) -> RecoveryProposal:
        rejected = proposal.model_copy(
            update={
                "status": RecoveryProposalStatus.REJECTED,
                "decided_at": _utc_timestamp(),
            },
            deep=True,
        )
        repositories.recovery_proposals.update(rejected.id, rejected)
        return rejected

    def _maybe_create_recovery_proposal(
        self,
        *,
        event: LocationEvent,
        itinerary: Itinerary,
        preference_version: int,
        repositories: ActiveEventRepositoryBundle,
    ) -> RecoveryProposal | None:
        if not event.deviation_detected:
            return None
        proposal = RecoveryProposal(
            id=f"recovery-{uuid4().hex}",
            itinerary_id=itinerary.id,
            user_id=event.user_id,
            reason="A deviation signal was detected during the active itinerary.",
            proposed_changes_summary=(
                "Review the remaining day and ask the user before applying any route or stop changes."
            ),
            source_location_event_id=event.id,
            preference_version=preference_version,
            created_at=_utc_timestamp(),
        )
        repositories.recovery_proposals.create(proposal.id, proposal)
        return proposal


def get_active_event_workflow_service() -> ActiveEventWorkflowService:
    return ActiveEventWorkflowService()


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()
