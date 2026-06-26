from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field

from app.services.booking_calls import BookingCallOffer, BookingCallRecord, BookingDetails
from app.domain.models import (
    GeoPoint,
    BudgetPosture,
    DayPlan,
    DayRhythm,
    Itinerary,
    ItineraryStatus,
    TravelPace,
    TravelPreferences,
    TripBrief,
    LocationEvent,
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


class LocationEventRequest(BaseModel):
    location: GeoPoint
    occurred_at: str
    speed_meters_per_second: float | None = Field(default=None, ge=0)
    heading_degrees: float | None = Field(default=None, ge=0, le=360)
    active_stop_id: str | None = None
    context_signal: str | None = None
    deviation_detected: bool = False

    def to_location_event(
        self,
        *,
        event_id: str,
        itinerary_id: str,
        user_id: str,
    ) -> LocationEvent:
        return LocationEvent(
            id=event_id,
            itinerary_id=itinerary_id,
            user_id=user_id,
            location=self.location,
            occurred_at=self.occurred_at,
            speed_meters_per_second=self.speed_meters_per_second,
            heading_degrees=self.heading_degrees,
            active_stop_id=self.active_stop_id,
            context_signal=self.context_signal,
            deviation_detected=self.deviation_detected,
        )


class PlacesAutocompleteSuggestionSchema(BaseModel):
    place_id: str
    description: str


class PlacesAutocompleteResponse(BaseModel):
    suggestions: list[PlacesAutocompleteSuggestionSchema]


class StopCoordinateSchema(BaseModel):
    lat: float
    lng: float


class RouteRequest(BaseModel):
    day_index: int = Field(ge=0)
    modes: list[str] = Field(default_factory=list, max_length=4)


class RouteSegmentSchema(BaseModel):
    from_stop_index: int
    to_stop_index: int
    mode: str
    duration_seconds: int
    distance_meters: int
    encoded_polyline: str


class StopCoordinateResult(BaseModel):
    index: int
    name: str
    lat: float
    lng: float


class RouteSegmentsResponse(BaseModel):
    segments: list[RouteSegmentSchema]
    stop_coordinates: list[StopCoordinateResult]


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    day_index: int = Field(ge=0)
    insert_before_index: int | None = Field(default=None, ge=0)
    scope: str | None = Field(default=None, max_length=40)
    target_stop_index: int | None = Field(default=None, ge=0)


class ChatRecommendationSchema(BaseModel):
    title: str
    description: str
    confidence: str = "medium"
    sources: list[str] = Field(default_factory=list)


class ChatProposalSchema(BaseModel):
    title: str
    summary: str
    proposed_itinerary: Itinerary


class ChatResponse(BaseModel):
    agent_message: str
    action: str | None = None
    updated_itinerary: Itinerary | None = None
    recommendations: list[ChatRecommendationSchema] = Field(default_factory=list)
    proposal: ChatProposalSchema | None = None
    booking_call_offer: BookingCallOffer | None = None
    booking_fallback: dict[str, str] | None = None


class BookingCallCreateRequest(BaseModel):
    day_index: int = Field(ge=0)
    stop_index: int = Field(ge=0)
    details: BookingDetails
    confirmed: bool = False


class BookingCallStatusResponse(BaseModel):
    call: BookingCallRecord


class RecoveryDecisionResponse(BaseModel):
    proposal_id: str
    status: str


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_preferences(user_id: str) -> TravelPreferences:
    return TravelPreferences(user_id=user_id)
