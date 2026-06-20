from collections.abc import Iterable
from dataclasses import dataclass

from app.domain.models import (
    AgentActionType,
    Itinerary,
    ItineraryPreferencePattern,
    ItineraryStatus,
    LifecycleResult,
    Recommendation,
    ServiceCommand,
    ServiceCommandType,
    SourceConfidence,
    SourceType,
    TravelPreferences,
)


class GuardrailViolation(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class GuardrailDecision:
    allowed: bool
    code: str
    message: str


STOP_ACTIVE_COMMANDS = (
    ServiceCommandType.STOP_LOCATION_COLLECTION,
    ServiceCommandType.STOP_EVENT_INGESTION,
    ServiceCommandType.STOP_AMBIENT_WORKFLOWS,
    ServiceCommandType.STOP_ACTIVE_SUGGESTIONS,
    ServiceCommandType.STOP_DYNAMIC_BEHAVIOR_UPDATES,
)

START_ACTIVE_COMMANDS = (
    ServiceCommandType.START_LOCATION_COLLECTION,
    ServiceCommandType.START_EVENT_INGESTION,
    ServiceCommandType.START_AMBIENT_WORKFLOWS,
    ServiceCommandType.START_ACTIVE_SUGGESTIONS,
    ServiceCommandType.START_DYNAMIC_BEHAVIOR_UPDATES,
)


class ItineraryLifecycleService:
    def start_itinerary(
        self,
        itineraries: Iterable[Itinerary],
        target_itinerary_id: str,
        *,
        confirm_replace: bool = False,
    ) -> LifecycleResult:
        copied = [itinerary.model_copy(deep=True) for itinerary in itineraries]
        target = self._find(copied, target_itinerary_id)
        if target.status == ItineraryStatus.COMPLETED:
            raise GuardrailViolation(
                "completed_itinerary_cannot_start",
                "Completed itineraries cannot be started again.",
            )

        active = [item for item in copied if item.status == ItineraryStatus.ACTIVE]
        active_other = next((item for item in active if item.id != target_itinerary_id), None)
        if active_other and not confirm_replace:
            return LifecycleResult(
                itineraries=copied,
                replacement_required=True,
                replaced_itinerary_id=active_other.id,
            )

        commands: list[ServiceCommand] = []
        if active_other:
            active_other.status = ItineraryStatus.INACTIVE
            commands.extend(self._commands(active_other.id, STOP_ACTIVE_COMMANDS))

        if target.status != ItineraryStatus.ACTIVE:
            target.status = ItineraryStatus.ACTIVE
            commands.extend(self._commands(target.id, START_ACTIVE_COMMANDS))

        self._assert_single_active(copied)
        return LifecycleResult(
            itineraries=copied,
            service_commands=commands,
            replaced_itinerary_id=active_other.id if active_other else None,
        )

    def stop_itinerary(
        self,
        itineraries: Iterable[Itinerary],
        target_itinerary_id: str,
    ) -> LifecycleResult:
        copied = [itinerary.model_copy(deep=True) for itinerary in itineraries]
        target = self._find(copied, target_itinerary_id)
        commands: list[ServiceCommand] = []

        if target.status == ItineraryStatus.ACTIVE:
            target.status = ItineraryStatus.INACTIVE
            commands.extend(self._commands(target.id, STOP_ACTIVE_COMMANDS))

        self._assert_single_active(copied)
        return LifecycleResult(itineraries=copied, service_commands=commands)

    def complete_itinerary(
        self,
        itineraries: Iterable[Itinerary],
        target_itinerary_id: str,
        *,
        user_initiated: bool,
    ) -> LifecycleResult:
        if not user_initiated:
            raise GuardrailViolation(
                "completion_requires_user_action",
                "Itineraries must only be completed by explicit user action.",
            )

        copied = [itinerary.model_copy(deep=True) for itinerary in itineraries]
        target = self._find(copied, target_itinerary_id)
        was_active = target.status == ItineraryStatus.ACTIVE
        target.status = ItineraryStatus.COMPLETED
        commands = self._commands(target.id, STOP_ACTIVE_COMMANDS) if was_active else []

        self._assert_single_active(copied)
        return LifecycleResult(itineraries=copied, service_commands=commands)

    def assert_can_ingest_active_event(self, itinerary: Itinerary) -> None:
        if itinerary.status != ItineraryStatus.ACTIVE:
            raise GuardrailViolation(
                "inactive_itinerary_event_rejected",
                "Location events and active suggestions are allowed only for ACTIVE itineraries.",
            )

    def assert_can_run_active_workflow(self, itinerary: Itinerary) -> None:
        if itinerary.status != ItineraryStatus.ACTIVE:
            raise GuardrailViolation(
                "active_workflow_rejected",
                "Ambient agents and active workflows must not run for INACTIVE or COMPLETED itineraries.",
            )

    @staticmethod
    def _commands(
        itinerary_id: str,
        commands: Iterable[ServiceCommandType],
    ) -> list[ServiceCommand]:
        return [ServiceCommand(itinerary_id=itinerary_id, command=command) for command in commands]

    @staticmethod
    def _find(itineraries: list[Itinerary], itinerary_id: str) -> Itinerary:
        for itinerary in itineraries:
            if itinerary.id == itinerary_id:
                return itinerary
        raise GuardrailViolation("itinerary_not_found", f"Itinerary {itinerary_id} was not found.")

    @staticmethod
    def _assert_single_active(itineraries: Iterable[Itinerary]) -> None:
        active_count = sum(1 for itinerary in itineraries if itinerary.status == ItineraryStatus.ACTIVE)
        if active_count > 1:
            raise GuardrailViolation(
                "multiple_active_itineraries",
                "Only one itinerary can be ACTIVE at a time.",
            )


class PreferenceService:
    def update_onboarding_preferences(
        self,
        preferences: TravelPreferences,
        **updates: object,
    ) -> TravelPreferences:
        allowed_updates = {
            key: value
            for key, value in updates.items()
            if key
            in {
                "pace",
                "pace_description",
                "interests",
                "budget_posture",
                "dietary_preferences",
                "accessibility_needs",
                "day_rhythm",
                "social_discovery_enabled",
                "agent_summary",
            }
        }
        return preferences.model_copy(
            update={
                **allowed_updates,
                "version": preferences.version + 1,
                "onboarding_required": False,
            },
            deep=True,
        )

    def reset_onboarding_preferences(self, preferences: TravelPreferences) -> TravelPreferences:
        return TravelPreferences(
            user_id=preferences.user_id,
            version=preferences.version + 1,
            onboarding_required=True,
            saved_itinerary_patterns=preferences.saved_itinerary_patterns,
        )

    def add_itinerary_pattern(
        self,
        preferences: TravelPreferences,
        pattern: ItineraryPreferencePattern,
        *,
        explicit_user_action: bool,
    ) -> TravelPreferences:
        if not explicit_user_action:
            raise GuardrailViolation(
                "preference_pattern_requires_user_action",
                "Adding an itinerary preference pattern requires explicit user action.",
            )

        patterns = [
            existing
            for existing in preferences.saved_itinerary_patterns
            if existing.id != pattern.id
        ]
        patterns.append(pattern)
        return preferences.model_copy(
            update={
                "saved_itinerary_patterns": patterns,
                "version": preferences.version + 1,
            },
            deep=True,
        )

    def delete_itinerary_pattern(
        self,
        preferences: TravelPreferences,
        pattern_id: str,
    ) -> TravelPreferences:
        return preferences.model_copy(
            update={
                "saved_itinerary_patterns": [
                    pattern
                    for pattern in preferences.saved_itinerary_patterns
                    if pattern.id != pattern_id
                ],
                "version": preferences.version + 1,
            },
            deep=True,
        )

    def assert_preference_version_is_current(
        self,
        *,
        workflow_preference_version: int,
        current_preference_version: int,
    ) -> None:
        if workflow_preference_version != current_preference_version:
            raise GuardrailViolation(
                "stale_preference_version",
                "Active services must re-read the latest preference version before suggesting.",
            )


class ActionGuardrailService:
    ACTIONS_REQUIRING_CONFIRMATION = {
        AgentActionType.ACTIVATE_ITINERARY,
        AgentActionType.STOP_ITINERARY,
        AgentActionType.COMPLETE_ITINERARY,
        AgentActionType.DELETE_ITINERARY,
        AgentActionType.EXPORT_ITINERARY,
        AgentActionType.BOOK,
        AgentActionType.BUY,
        AgentActionType.PLACE_CALL,
        AgentActionType.APPLY_RECOVERY,
        AgentActionType.ADD_ITINERARY_PATTERN,
    }

    def assert_explicit_confirmation(
        self,
        action: AgentActionType,
        *,
        confirmed: bool,
    ) -> None:
        if action in self.ACTIONS_REQUIRING_CONFIRMATION and not confirmed:
            raise GuardrailViolation(
                "explicit_confirmation_required",
                f"{action.value} requires explicit user confirmation.",
            )


class RecommendationGuardrailService:
    SOCIAL_SOURCE_TYPES = {
        SourceType.TIKTOK_API,
        SourceType.INSTAGRAM_GRAPH_API,
    }

    def validate_recommendation(self, recommendation: Recommendation) -> GuardrailDecision:
        if not recommendation.explanation.strip():
            return GuardrailDecision(
                False,
                "recommendation_explanation_required",
                "Recommendations require a brief explanation.",
            )

        if self._is_low_confidence_social_only(recommendation):
            return GuardrailDecision(
                False,
                "social_only_low_confidence",
                "Social sources are discovery signals only and need verification before recommendation.",
            )

        return GuardrailDecision(True, "allowed", "Recommendation satisfies guardrails.")

    def _is_low_confidence_social_only(self, recommendation: Recommendation) -> bool:
        if recommendation.confidence != SourceConfidence.LOW or not recommendation.evidence:
            return False
        return all(
            evidence.is_social_signal or evidence.source_type in self.SOCIAL_SOURCE_TYPES
            for evidence in recommendation.evidence
        )
