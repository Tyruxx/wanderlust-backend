from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import time
from typing import Any, Protocol
from uuid import uuid4

from google import genai
from google.adk import Agent
from pydantic import BaseModel, Field

from app.core.settings import get_settings
from app.domain.models import (
    DayPlan,
    Itinerary,
    ItineraryStatus,
    PlaceStop,
    Recommendation,
    SourceConfidence,
    SourceEvidence,
    TravelPreferences,
    TripBrief,
)
from app.services.guardrails import RecommendationGuardrailService
from app.services.maps import CandidatePlace, GoogleMapsClient, MapsIntegrationError


class PlanningWorkflowError(RuntimeError):
    pass


class PlannerClient(Protocol):
    def generate_plan(
        self,
        *,
        brief: TripBrief,
        preferences: TravelPreferences,
        candidates: list[CandidatePlace],
        weather: dict[str, Any] | None,
    ) -> dict[str, Any]:
        ...


class PlannerStopOutput(BaseModel):
    name: str
    suggested_order: int = Field(ge=1)
    time_window: str | None = None
    what_to_do: str
    explanation: str
    category: str = "place"
    confidence: SourceConfidence = SourceConfidence.MEDIUM
    travel_time_assumption_minutes: int | None = Field(default=None, ge=0)


class PlannerDayOutput(BaseModel):
    day_number: int = Field(ge=1)
    start_location: str
    end_location: str
    start_time: str = "09:00"
    end_time: str = "18:00"
    stops: list[PlannerStopOutput] = Field(default_factory=list)
    backup_options: list[str] = Field(default_factory=list)


class PlannerItineraryOutput(BaseModel):
    title: str
    days: list[PlannerDayOutput]


@dataclass(frozen=True)
class PlanningResult:
    itinerary: Itinerary
    evidence: list[SourceEvidence]
    recommendations: list[Recommendation]
    agent_names: list[str]


class ADKPlanningAgents:
    def __init__(self, model: str) -> None:
        self.trip_intake = Agent(
            name="trip_intake_agent",
            model=model,
            description="Normalize trip brief and preference constraints.",
            instruction="Convert travel requirements into structured constraints without inventing facts.",
        )
        self.place_discovery = Agent(
            name="place_discovery_agent",
            model=model,
            description="Select candidate places from compliant source evidence.",
            instruction="Use Google Maps and compliant sources as discovery evidence.",
        )
        self.verification = Agent(
            name="verification_agent",
            model=model,
            description="Validate place facts, confidence, and source quality.",
            instruction="Reject low-confidence social-only recommendations and explain uncertainty.",
        )
        self.planner = Agent(
            name="itinerary_planner_agent",
            model=model,
            description="Build day-by-day itinerary plans with explanations.",
            instruction="Create realistic day plans with mandatory explanation and confidence per stop.",
        )

    @property
    def all(self) -> list[Agent]:
        return [self.trip_intake, self.place_discovery, self.verification, self.planner]

    @property
    def names(self) -> list[str]:
        return [agent.name for agent in self.all]


class GeminiPlannerClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.model = settings.gemini_model
        self.use_vertex_ai = settings.use_vertex_ai
        self.google_api_key = settings.google_api_key
        self._client: genai.Client | None = None

    def _get_client(self) -> genai.Client:
        if self._client is not None:
            return self._client
        if self.use_vertex_ai:
            try:
                import google.auth

                google.auth.default()
            except Exception:
                if self.google_api_key:
                    self._client = genai.Client(
                        vertexai=False,
                        api_key=self.google_api_key,
                    )
                    return self._client
                raise PlanningWorkflowError(
                    "Vertex AI credentials not configured. "
                    "Run `gcloud auth application-default login` "
                    "or set GOOGLE_API_KEY in .env and USE_VERTEX_AI=false"
                )
        self._client = genai.Client(
            vertexai=False,
            api_key=self.google_api_key,
        )
        return self._client

    def generate_plan(
        self,
        *,
        brief: TripBrief,
        preferences: TravelPreferences,
        candidates: list[CandidatePlace],
        weather: dict[str, Any] | None,
    ) -> dict[str, Any]:
        prompt = _planner_prompt(brief, preferences, candidates, weather)
        client = self._get_client()
        response = client.models.generate_content(
            model=self.model,
            contents=prompt,
        )
        text = getattr(response, "text", None)
        if not text:
            raise PlanningWorkflowError("Gemini returned an empty planning response.")
        return _parse_json_response(text)


class ADKPlanningWorkflowService:
    def __init__(
        self,
        *,
        maps_client: GoogleMapsClient | None = None,
        planner_client: PlannerClient | None = None,
    ) -> None:
        settings = get_settings()
        self.maps_client = maps_client or GoogleMapsClient()
        self.planner_client = planner_client or GeminiPlannerClient()
        self.agents = ADKPlanningAgents(settings.gemini_model)
        self.recommendation_guardrails = RecommendationGuardrailService()

    def generate_itinerary(
        self,
        *,
        user_id: str,
        brief: TripBrief,
        preferences: TravelPreferences,
    ) -> PlanningResult:
        candidates = self._discover_candidates(brief, preferences)
        weather = self._load_weather_context(brief)
        planner_output = PlannerItineraryOutput.model_validate(
            self.planner_client.generate_plan(
                brief=brief,
                preferences=preferences,
                candidates=candidates,
                weather=weather,
            )
        )
        evidence = [candidate.to_evidence() for candidate in candidates]
        recommendations: list[Recommendation] = []
        days: list[DayPlan] = []

        for day in planner_output.days:
            stops: list[PlaceStop] = []
            for stop in day.stops:
                recommendation = Recommendation(
                    id=f"rec-{user_id}-{day.day_number}-{stop.suggested_order}",
                    title=stop.name,
                    category=stop.category,
                    explanation=stop.explanation,
                    confidence=stop.confidence,
                    evidence=evidence[:3],
                )
                decision = self.recommendation_guardrails.validate_recommendation(recommendation)
                if not decision.allowed:
                    continue
                recommendations.append(recommendation)
                stops.append(
                    PlaceStop(
                        id=f"stop-{user_id}-{day.day_number}-{stop.suggested_order}",
                        name=stop.name,
                        suggested_order=stop.suggested_order,
                        time_window=stop.time_window,
                        what_to_do=stop.what_to_do,
                        travel_time_assumption_minutes=stop.travel_time_assumption_minutes,
                        recommendations=[recommendation],
                    )
                )

            days.append(
                DayPlan(
                    day_number=day.day_number,
                    start_location=day.start_location,
                    end_location=day.end_location,
                    start_time=_parse_time(day.start_time),
                    end_time=_parse_time(day.end_time),
                    stops=stops,
                    backup_options=day.backup_options,
                )
            )

        itinerary = Itinerary(
            id=f"itin-{uuid4().hex}",
            user_id=user_id,
            title=planner_output.title or f"{brief.region} itinerary",
            status=ItineraryStatus.INACTIVE,
            brief=brief,
            preference_version=preferences.version,
            days=days,
        )
        return PlanningResult(
            itinerary=itinerary,
            evidence=evidence,
            recommendations=recommendations,
            agent_names=self.agents.names,
        )

    def _discover_candidates(
        self,
        brief: TripBrief,
        preferences: TravelPreferences,
    ) -> list[CandidatePlace]:
        queries = _candidate_queries(brief, preferences)
        candidates: list[CandidatePlace] = []
        for query in queries:
            try:
                candidates.extend(self.maps_client.text_search(query, region=brief.region))
            except MapsIntegrationError:
                raise
            except Exception as exc:
                raise PlanningWorkflowError(f"Maps place discovery failed for query: {query}") from exc
        deduped: dict[str, CandidatePlace] = {}
        for candidate in candidates:
            key = candidate.place_id or f"{candidate.name}:{candidate.formatted_address}"
            deduped[key] = candidate
        return list(deduped.values())[:18]

    def _load_weather_context(self, brief: TripBrief) -> dict[str, Any] | None:
        try:
            coordinates = self.maps_client.geocode(brief.region)
            if coordinates is None:
                return None
            return self.maps_client.current_weather(coordinates)
        except MapsIntegrationError:
            raise
        except Exception:
            return None


def get_planning_workflow_service() -> ADKPlanningWorkflowService:
    return ADKPlanningWorkflowService()


def _candidate_queries(brief: TripBrief, preferences: TravelPreferences) -> list[str]:
    interests = brief.style_interests or preferences.interests or ["food", "attractions", "local culture"]
    return list(dict.fromkeys([*interests, *brief.must_visit_places]))[:5]


def _planner_prompt(
    brief: TripBrief,
    preferences: TravelPreferences,
    candidates: list[CandidatePlace],
    weather: dict[str, Any] | None,
) -> str:
    return json.dumps(
        {
            "task": "Create a realistic day-by-day travel itinerary as strict JSON.",
            "schema": {
                "title": "string",
                "days": [
                    {
                        "day_number": 1,
                        "start_location": "string",
                        "end_location": "string",
                        "start_time": "HH:MM",
                        "end_time": "HH:MM",
                        "stops": [
                            {
                                "name": "string",
                                "suggested_order": 1,
                                "time_window": "string",
                                "what_to_do": "string",
                                "explanation": "brief reason required",
                                "category": "string",
                                "confidence": "high|medium|low",
                                "travel_time_assumption_minutes": 15,
                            }
                        ],
                        "backup_options": ["string"],
                    }
                ],
            },
            "guardrails": [
                "Every recommendation must include explanation and confidence.",
                "Use candidate places and constraints; do not invent unsupported place facts.",
                "Do not include bookings, purchases, or calls.",
                "Low-confidence or social-only claims must be marked exploratory or omitted.",
            ],
            "brief": brief.model_dump(mode="json"),
            "preferences": preferences.model_dump(mode="json"),
            "candidate_places": [candidate.model_dump(mode="json") for candidate in candidates],
            "weather": weather,
        },
        ensure_ascii=True,
    )


def _parse_json_response(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise PlanningWorkflowError("Planner response was not valid JSON.") from exc


def _parse_time(value: str) -> time:
    try:
        hour, minute = value.split(":", maxsplit=1)
        return time(int(hour), int(minute))
    except Exception as exc:
        raise PlanningWorkflowError(f"Invalid planner time value: {value}") from exc
