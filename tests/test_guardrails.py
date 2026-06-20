import unittest
from datetime import time

from app.domain.models import (
    AgentActionType,
    BudgetPosture,
    DayPlan,
    Itinerary,
    ItineraryPreferencePattern,
    ItineraryStatus,
    Recommendation,
    SourceConfidence,
    SourceEvidence,
    SourceType,
    TravelPace,
    TravelPreferences,
    TripBrief,
)
from app.services.guardrails import (
    ActionGuardrailService,
    GuardrailViolation,
    ItineraryLifecycleService,
    PreferenceService,
    RecommendationGuardrailService,
)


def make_itinerary(
    itinerary_id: str,
    *,
    status: ItineraryStatus = ItineraryStatus.INACTIVE,
) -> Itinerary:
    return Itinerary(
        id=itinerary_id,
        user_id="user-1",
        title=f"Trip {itinerary_id}",
        status=status,
        preference_version=1,
        brief=TripBrief(
            region="Singapore",
            description="Food and neighborhoods",
            trip_length_days=1,
        ),
        days=[
            DayPlan(
                day_number=1,
                start_location="Hotel",
                end_location="Hotel",
                start_time=time(9, 0),
                end_time=time(18, 0),
            )
        ],
    )


class GuardrailTests(unittest.TestCase):
    def test_start_requires_replacement_confirmation_when_another_itinerary_is_active(self) -> None:
        service = ItineraryLifecycleService()
        active = make_itinerary("active", status=ItineraryStatus.ACTIVE)
        target = make_itinerary("target")

        result = service.start_itinerary([active, target], "target")

        self.assertTrue(result.replacement_required)
        self.assertEqual(result.replaced_itinerary_id, "active")
        self.assertEqual(
            [item.status for item in result.itineraries],
            [ItineraryStatus.ACTIVE, ItineraryStatus.INACTIVE],
        )

    def test_start_with_confirmation_stops_previous_active_itinerary(self) -> None:
        service = ItineraryLifecycleService()
        active = make_itinerary("active", status=ItineraryStatus.ACTIVE)
        target = make_itinerary("target")

        result = service.start_itinerary([active, target], "target", confirm_replace=True)
        statuses = {item.id: item.status for item in result.itineraries}

        self.assertEqual(
            statuses,
            {
                "active": ItineraryStatus.INACTIVE,
                "target": ItineraryStatus.ACTIVE,
            },
        )
        self.assertEqual(result.replaced_itinerary_id, "active")
        self.assertEqual(
            len([item for item in result.itineraries if item.status == ItineraryStatus.ACTIVE]),
            1,
        )
        self.assertEqual(
            {command.itinerary_id for command in result.service_commands},
            {"active", "target"},
        )

    def test_stop_active_itinerary_disables_active_services(self) -> None:
        service = ItineraryLifecycleService()
        active = make_itinerary("active", status=ItineraryStatus.ACTIVE)

        result = service.stop_itinerary([active], "active")

        self.assertEqual(result.itineraries[0].status, ItineraryStatus.INACTIVE)
        self.assertEqual(
            {command.command.value for command in result.service_commands},
            {
                "stop_location_collection",
                "stop_event_ingestion",
                "stop_ambient_workflows",
                "stop_active_suggestions",
                "stop_dynamic_behavior_updates",
            },
        )

    def test_complete_requires_explicit_user_action(self) -> None:
        service = ItineraryLifecycleService()
        active = make_itinerary("active", status=ItineraryStatus.ACTIVE)

        with self.assertRaisesRegex(GuardrailViolation, "explicit user action"):
            service.complete_itinerary([active], "active", user_initiated=False)

    def test_inactive_and_completed_itineraries_reject_active_events(self) -> None:
        service = ItineraryLifecycleService()

        for status in (ItineraryStatus.INACTIVE, ItineraryStatus.COMPLETED):
            with self.assertRaises(GuardrailViolation):
                service.assert_can_ingest_active_event(make_itinerary("trip", status=status))

        service.assert_can_ingest_active_event(make_itinerary("trip", status=ItineraryStatus.ACTIVE))

    def test_preference_updates_increment_version_and_clear_onboarding_requirement(self) -> None:
        service = PreferenceService()
        preferences = TravelPreferences(user_id="user-1", version=3, onboarding_required=True)

        updated = service.update_onboarding_preferences(
            preferences,
            pace=TravelPace.RELAXED,
            interests=["ramen", "photography"],
            budget_posture=BudgetPosture.MID_RANGE,
        )

        self.assertEqual(updated.version, 4)
        self.assertFalse(updated.onboarding_required)
        self.assertEqual(updated.interests, ["ramen", "photography"])

    def test_reset_preferences_requires_onboarding_but_keeps_saved_patterns(self) -> None:
        service = PreferenceService()
        pattern = ItineraryPreferencePattern(
            id="pattern-1",
            itinerary_id="itinerary-1",
            name="Quiet Tokyo",
            interests=["coffee"],
            source_preference_version=2,
        )
        preferences = TravelPreferences(
            user_id="user-1",
            version=4,
            onboarding_required=False,
            saved_itinerary_patterns=[pattern],
        )

        reset = service.reset_onboarding_preferences(preferences)

        self.assertEqual(reset.version, 5)
        self.assertTrue(reset.onboarding_required)
        self.assertEqual(reset.saved_itinerary_patterns, [pattern])

    def test_add_itinerary_pattern_requires_explicit_user_action(self) -> None:
        service = PreferenceService()
        preferences = TravelPreferences(user_id="user-1", version=1)
        pattern = ItineraryPreferencePattern(
            id="pattern-1",
            itinerary_id="itinerary-1",
            name="Quiet Tokyo",
            source_preference_version=1,
        )

        with self.assertRaisesRegex(GuardrailViolation, "explicit user action"):
            service.add_itinerary_pattern(preferences, pattern, explicit_user_action=False)

        updated = service.add_itinerary_pattern(preferences, pattern, explicit_user_action=True)
        self.assertEqual(updated.version, 2)
        self.assertEqual(updated.saved_itinerary_patterns, [pattern])

    def test_stale_preference_version_is_rejected(self) -> None:
        service = PreferenceService()

        with self.assertRaisesRegex(GuardrailViolation, "latest preference version"):
            service.assert_preference_version_is_current(
                workflow_preference_version=1,
                current_preference_version=2,
            )

    def test_agent_actions_that_change_state_require_confirmation(self) -> None:
        service = ActionGuardrailService()

        with self.assertRaises(GuardrailViolation):
            service.assert_explicit_confirmation(AgentActionType.BOOK, confirmed=False)

        service.assert_explicit_confirmation(AgentActionType.BOOK, confirmed=True)

    def test_social_only_low_confidence_recommendations_are_rejected(self) -> None:
        service = RecommendationGuardrailService()
        recommendation = Recommendation(
            id="rec-1",
            title="Trending cafe",
            category="cafe",
            explanation="Popular in recent short-form videos.",
            confidence=SourceConfidence.LOW,
            evidence=[
                SourceEvidence(
                    source_type=SourceType.TIKTOK_API,
                    title="Creator mention",
                    confidence=SourceConfidence.LOW,
                    is_social_signal=True,
                )
            ],
        )

        decision = service.validate_recommendation(recommendation)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "social_only_low_confidence")


if __name__ == "__main__":
    unittest.main()
