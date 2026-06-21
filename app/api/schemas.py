from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field

from app.domain.models import (
    BudgetPosture,
    DayPlan,
    DayRhythm,
    Itinerary,
    ItineraryStatus,
    TravelPace,
    TravelPreferences,
    TripBrief,
)


class PreferenceUpdateRequest(BaseModel):
    pace: TravelPace | None = None
    pace_description: str | None = None
    interests: list[str] | None = None
    budget_posture: BudgetPosture | None = None
    dietary_preferences: list[str] | None = None
    accessibility_needs: list[str] | None = None
    day_rhythm: DayRhythm | None = None
    social_discovery_enabled: bool | None = None
    agent_summary: str | None = None

    def to_update_dict(self) -> dict[str, object]:
        return self.model_dump(exclude_none=True)


class ItineraryCreateRequest(BaseModel):
    title: str
    brief: TripBrief
    days: list[DayPlan] = Field(default_factory=list)

    def to_itinerary(self, user_id: str, preference_version: int) -> Itinerary:
        itinerary_id = f"itin-{uuid4().hex}"
        return Itinerary(
            id=itinerary_id,
            user_id=user_id,
            title=self.title,
            status=ItineraryStatus.INACTIVE,
            brief=self.brief,
            preference_version=preference_version,
            days=self.days,
        )


class ItineraryUpdateRequest(BaseModel):
    title: str | None = None
    brief: TripBrief | None = None
    days: list[DayPlan] | None = None


class ExportRequestResponse(BaseModel):
    export_request_id: str
    itinerary_id: str
    status: str = "accepted"


class DeleteResponse(BaseModel):
    deleted: bool
    itinerary_id: str


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_preferences(user_id: str) -> TravelPreferences:
    return TravelPreferences(user_id=user_id)
