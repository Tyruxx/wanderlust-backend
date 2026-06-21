from __future__ import annotations

import os
import sys

from app.domain.models import TravelPreferences, TripBrief
from app.services.planning import ADKPlanningWorkflowService


def main() -> int:
    if os.getenv("RUN_REAL_INTEGRATION") != "1":
        print("Skipped. Set RUN_REAL_INTEGRATION=1 to call real Maps and Vertex/Gemini services.")
        return 0

    service = ADKPlanningWorkflowService()
    result = service.generate_itinerary(
        user_id="smoke-user",
        brief=TripBrief(
            region=os.getenv("SMOKE_TRIP_REGION", "Singapore"),
            description="Two-day food, culture, and photography trip.",
            trip_length_days=2,
            style_interests=["food", "photography", "local culture"],
        ),
        preferences=TravelPreferences(
            user_id="smoke-user",
            version=1,
            onboarding_required=False,
            interests=["food", "photography", "local culture"],
            social_discovery_enabled=False,
        ),
    )
    if not result.itinerary.days:
        raise RuntimeError("Smoke planning returned no day plans.")
    if not result.recommendations:
        raise RuntimeError("Smoke planning returned no validated recommendations.")
    print(
        {
            "title": result.itinerary.title,
            "days": len(result.itinerary.days),
            "recommendations": len(result.recommendations),
            "evidence": len(result.evidence),
            "agents": result.agent_names,
        }
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
