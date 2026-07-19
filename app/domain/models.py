from __future__ import annotations

from datetime import date, time
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ItineraryStatus(str, Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    COMPLETED = "COMPLETED"


class TravelPace(str, Enum):
    RELAXED = "relaxed"
    BALANCED = "balanced"
    PACKED = "packed"
    USER_DESCRIBED = "user_described"


class BudgetPosture(str, Enum):
    BUDGET = "budget"
    MID_RANGE = "mid_range"
    MODERATE = "mid_range"  # alias
    LUXURY = "luxury"
    FLEXIBLE = "flexible"

    @classmethod
    def _missing_(cls, value: str) -> object | None:
        aliases = {"moderate": "mid_range"}
        normalised = aliases.get(value.lower())
        if normalised is not None:
            return cls(normalised)
        return None


class DayRhythm(str, Enum):
    EARLY_START = "early_start"
    LATE_START = "late_start"
    AFTERNOON_BREAK = "afternoon_break"
    LATE_NIGHT = "late_night"
    FLEXIBLE = "flexible"

    @classmethod
    def _missing_(cls, value: str) -> object | None:
        aliases = {"early start": "early_start"}
        normalised = aliases.get(value.lower())
        if normalised is not None:
            return cls(normalised)
        return None


class SourceType(str, Enum):
    GOOGLE_PLACES = "google_places"
    GOOGLE_ROUTES = "google_routes"
    GOOGLE_GEOCODING = "google_geocoding"
    GOOGLE_WEATHER = "google_weather"
    GOOGLE_SEARCH_GROUNDING = "google_search_grounding"
    OFFICIAL_WEBSITE = "official_website"
    TIKTOK_API = "tiktok_api"
    INSTAGRAM_GRAPH_API = "instagram_graph_api"
    USER_PROVIDED = "user_provided"
    OTHER = "other"


class SourceConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AgentActionType(str, Enum):
    ACTIVATE_ITINERARY = "activate_itinerary"
    STOP_ITINERARY = "stop_itinerary"
    COMPLETE_ITINERARY = "complete_itinerary"
    DELETE_ITINERARY = "delete_itinerary"
    EXPORT_ITINERARY = "export_itinerary"
    BOOK = "book"
    BUY = "buy"
    PLACE_CALL = "place_call"
    APPLY_RECOVERY = "apply_recovery"
    ADD_ITINERARY_PATTERN = "add_itinerary_pattern"


class RecoveryProposalStatus(str, Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class ServiceCommandType(str, Enum):
    START_LOCATION_COLLECTION = "start_location_collection"
    STOP_LOCATION_COLLECTION = "stop_location_collection"
    START_EVENT_INGESTION = "start_event_ingestion"
    STOP_EVENT_INGESTION = "stop_event_ingestion"
    START_AMBIENT_WORKFLOWS = "start_ambient_workflows"
    STOP_AMBIENT_WORKFLOWS = "stop_ambient_workflows"
    START_ACTIVE_SUGGESTIONS = "start_active_suggestions"
    STOP_ACTIVE_SUGGESTIONS = "stop_active_suggestions"
    START_DYNAMIC_BEHAVIOR_UPDATES = "start_dynamic_behavior_updates"
    STOP_DYNAMIC_BEHAVIOR_UPDATES = "stop_dynamic_behavior_updates"


class DomainModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)


class ItineraryPreferencePattern(DomainModel):
    id: str
    itinerary_id: str
    name: str
    interests: list[str] = Field(default_factory=list)
    pace: TravelPace = TravelPace.BALANCED
    budget_posture: BudgetPosture = BudgetPosture.FLEXIBLE
    day_rhythm: DayRhythm = DayRhythm.FLEXIBLE
    constraints: list[str] = Field(default_factory=list)
    source_preference_version: int = Field(ge=1)


class TravelPreferences(DomainModel):
    user_id: str
    version: int = Field(default=1, ge=1)
    onboarding_required: bool = True
    pace: TravelPace = TravelPace.BALANCED
    pace_description: str | None = None
    interests: list[str] = Field(default_factory=list)
    budget_posture: BudgetPosture = BudgetPosture.FLEXIBLE
    dietary_preferences: list[str] = Field(default_factory=list)
    accessibility_needs: list[str] = Field(default_factory=list)
    day_rhythm: DayRhythm = DayRhythm.FLEXIBLE
    social_discovery_enabled: bool = False
    saved_itinerary_patterns: list[ItineraryPreferencePattern] = Field(default_factory=list)
    agent_summary: str | None = None

    @model_validator(mode="after")
    def require_description_for_user_described_pace(self) -> "TravelPreferences":
        if self.pace == TravelPace.USER_DESCRIBED and not self.pace_description:
            raise ValueError("pace_description is required when pace is user_described")
        return self


class DynamicBehaviorPreferences(DomainModel):
    itinerary_id: str
    version: int = Field(default=1, ge=1)
    pace_adjustment_factor: float = Field(default=1.0, ge=0.25, le=4.0)
    dwell_time_tendencies: dict[str, float] = Field(default_factory=dict)
    skipped_place_types: list[str] = Field(default_factory=list)
    extended_interest_place_types: list[str] = Field(default_factory=list)
    observed_meal_windows: list[str] = Field(default_factory=list)
    observed_rest_patterns: list[str] = Field(default_factory=list)
    confidence: SourceConfidence = SourceConfidence.LOW


class GeoPoint(DomainModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy_meters: float | None = Field(default=None, ge=0)


class LocationEvent(DomainModel):
    id: str
    itinerary_id: str
    user_id: str
    location: GeoPoint
    occurred_at: str
    speed_meters_per_second: float | None = Field(default=None, ge=0)
    heading_degrees: float | None = Field(default=None, ge=0, le=360)
    active_stop_id: str | None = None
    context_signal: str | None = None
    deviation_detected: bool = False


class RecoveryProposal(DomainModel):
    id: str
    itinerary_id: str
    user_id: str
    status: RecoveryProposalStatus = RecoveryProposalStatus.PENDING
    reason: str
    proposed_changes_summary: str
    source_location_event_id: str
    preference_version: int = Field(ge=1)
    requires_user_acceptance: bool = True
    created_at: str
    decided_at: str | None = None


class ActiveEventIngestionResult(DomainModel):
    accepted: bool
    itinerary_id: str
    published_event_id: str
    dynamic_preference_version: int
    recovery_proposal: RecoveryProposal | None = None


class DayRule(DomainModel):
    start_day: int = Field(ge=1)
    end_day: int = Field(ge=1)
    start_date: date | None = None
    end_date: date | None = None
    start_place: str = ""
    end_place: str = ""
    start_place_id: str | None = None
    end_place_id: str | None = None
    start_latitude: float | None = Field(default=None, ge=-90, le=90)
    start_longitude: float | None = Field(default=None, ge=-180, le=180)
    end_latitude: float | None = Field(default=None, ge=-90, le=90)
    end_longitude: float | None = Field(default=None, ge=-180, le=180)
    start_time: time
    end_time: time

    @model_validator(mode="after")
    def validate_ranges(self) -> "DayRule":
        if self.end_day < self.start_day:
            raise ValueError("end_day must be greater than or equal to start_day")
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be greater than or equal to start_date")
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be later than start_time")
        return self


class TripBrief(DomainModel):
    region: str
    description: str
    trip_length_days: int = Field(ge=1, le=60)
    include_regions: list[str] = Field(default_factory=list)
    avoid_regions: list[str] = Field(default_factory=list)
    traveler_count: int = Field(default=1, ge=1)
    traveler_type: str | None = None
    style_interests: list[str] = Field(default_factory=list)
    day_rules: list[DayRule] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    must_visit_places: list[str] = Field(default_factory=list)
    radius_km: float | None = Field(default=None, ge=0)
    preferred_transport_modes: list[str] = Field(default_factory=list)


class SourceEvidence(DomainModel):
    source_type: SourceType
    title: str
    url: str | None = None
    confidence: SourceConfidence
    freshness_note: str | None = None
    is_social_signal: bool = False
    claims: list[str] = Field(default_factory=list)


class Recommendation(DomainModel):
    id: str
    title: str
    category: str
    explanation: str
    confidence: SourceConfidence
    evidence: list[SourceEvidence] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("explanation")
    @classmethod
    def explanation_is_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("recommendations require a brief explanation")
        return value


class PlaceStop(DomainModel):
    id: str
    name: str
    suggested_order: int = Field(ge=1)
    time_window: str | None = None
    what_to_do: str
    travel_time_assumption_minutes: int | None = Field(default=None, ge=0)
    recommendations: list[Recommendation] = Field(default_factory=list)
    backup_options: list[str] = Field(default_factory=list)


class DayPlan(DomainModel):
    day_number: int = Field(ge=1)
    day_date: date | None = None
    start_location: str
    end_location: str
    start_time: time
    end_time: time
    stops: list[PlaceStop] = Field(default_factory=list)
    backup_options: list[str] = Field(default_factory=list)


class Itinerary(DomainModel):
    id: str
    user_id: str
    title: str
    status: ItineraryStatus = ItineraryStatus.INACTIVE
    brief: TripBrief
    preference_version: int = Field(ge=1)
    days: list[DayPlan] = Field(default_factory=list)
    dynamic_preferences: DynamicBehaviorPreferences | None = None


class ServiceCommand(DomainModel):
    itinerary_id: str
    command: ServiceCommandType


class LifecycleResult(DomainModel):
    itineraries: list[Itinerary]
    service_commands: list[ServiceCommand] = Field(default_factory=list)
    replacement_required: bool = False
    replaced_itinerary_id: str | None = None
