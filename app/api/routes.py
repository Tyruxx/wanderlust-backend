import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies import (
    RepositoryBundle,
    get_active_event_service,
    get_current_user,
    get_planning_service,
    get_repositories,
)
from app.api.schemas import (
    ChatRequest,
    ChatResponse,
    DeleteResponse,
    ExportRequestResponse,
    ItineraryCreateRequest,
    ItineraryUpdateRequest,
    LocationEventRequest,
    PlacesAutocompleteResponse,
    PlacesAutocompleteSuggestionSchema,
    PreferenceUpdateRequest,
    RecoveryDecisionResponse,
    RouteRequest,
    RouteSegmentSchema,
    RouteSegmentsResponse,
    StopCoordinateResult,
    default_preferences,
    utc_timestamp,
)
from app.domain.models import (
    AgentActionType,
    ActiveEventIngestionResult,
    Itinerary,
    ItineraryPreferencePattern,
    LifecycleResult,
    RecoveryProposal,
    TravelPreferences,
)
from app.services.active_events import ActiveEventRepositoryBundle, ActiveEventWorkflowService
from app.services.auth import VerifiedUser
from app.services.guardrails import (
    ActionGuardrailService,
    ItineraryLifecycleService,
    PreferenceService,
)
from app.services.repositories import AuditLogEntry
from app.services.maps import Coordinates, GoogleMapsClient, MapsIntegrationError
from app.services.planning import ADKPlanningWorkflowService, PlanningWorkflowError, sanitize_agent_message


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["v1"])


@router.get("/preferences", response_model=TravelPreferences)
def get_preferences(
    current_user: VerifiedUser = Depends(get_current_user),
    repositories: RepositoryBundle = Depends(get_repositories),
) -> TravelPreferences:
    logger.info("get_preferences user=%s", current_user.uid)
    return repositories.preferences.get_by_user(current_user.uid) or default_preferences(current_user.uid)


@router.put("/preferences", response_model=TravelPreferences)
def update_preferences(
    request: PreferenceUpdateRequest,
    current_user: VerifiedUser = Depends(get_current_user),
    repositories: RepositoryBundle = Depends(get_repositories),
) -> TravelPreferences:
    logger.info("update_preferences user=%s fields=%s", current_user.uid, request.to_update_dict())
    existing = repositories.preferences.get_by_user(current_user.uid) or default_preferences(current_user.uid)
    try:
        updated = PreferenceService().update_onboarding_preferences(existing, **request.to_update_dict())
    except Exception as exc:
        logger.exception("update_preferences failed for user=%s", current_user.uid)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    repositories.preferences.update(current_user.uid, updated)
    _audit(repositories, current_user.uid, "preferences.update", "preferences", current_user.uid)
    return updated


@router.post("/preferences/reset", response_model=TravelPreferences)
def reset_preferences(
    current_user: VerifiedUser = Depends(get_current_user),
    repositories: RepositoryBundle = Depends(get_repositories),
) -> TravelPreferences:
    logger.info("reset_preferences user=%s", current_user.uid)
    existing = repositories.preferences.get_by_user(current_user.uid) or default_preferences(current_user.uid)
    reset = PreferenceService().reset_onboarding_preferences(existing)
    repositories.preferences.update(current_user.uid, reset)
    _audit(repositories, current_user.uid, "preferences.reset", "preferences", current_user.uid)
    return reset


@router.get("/places/autocomplete", response_model=PlacesAutocompleteResponse)
def places_autocomplete(
    input: str = Query(min_length=1, max_length=200),
    types: str = Query(default="geocode"),
):
    logger.info("places_autocomplete input=%s types=%s", input, types)
    try:
        client = GoogleMapsClient()
        suggestions = client.places_autocomplete(input=input, types=types)
    except MapsIntegrationError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    except Exception:
        logger.exception("places_autocomplete failed input=%s", input)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Autocomplete service unavailable.")
    return PlacesAutocompleteResponse(
        suggestions=[
            PlacesAutocompleteSuggestionSchema(place_id=s.place_id, description=s.description)
            for s in suggestions
        ],
    )


@router.get("/itineraries", response_model=list[Itinerary])
def list_itineraries(
    current_user: VerifiedUser = Depends(get_current_user),
    repositories: RepositoryBundle = Depends(get_repositories),
) -> list[Itinerary]:
    logger.info("list_itineraries user=%s", current_user.uid)
    return repositories.itineraries.find_by_user(current_user.uid)


@router.post("/itineraries", response_model=Itinerary, status_code=status.HTTP_201_CREATED)
def create_itinerary(
    request: ItineraryCreateRequest,
    current_user: VerifiedUser = Depends(get_current_user),
    repositories: RepositoryBundle = Depends(get_repositories),
) -> Itinerary:
    logger.info("create_itinerary user=%s title=%s", current_user.uid, request.title)
    preferences = _require_completed_onboarding(current_user.uid, repositories)
    itinerary = request.to_itinerary(
        user_id=current_user.uid,
        preference_version=preferences.version,
    )
    repositories.itineraries.create(itinerary.id, itinerary)
    _audit(repositories, current_user.uid, "itinerary.create", "itinerary", itinerary.id)
    return itinerary


@router.post("/itineraries/generate", response_model=Itinerary, status_code=status.HTTP_201_CREATED)
def generate_itinerary(
    request: ItineraryCreateRequest,
    current_user: VerifiedUser = Depends(get_current_user),
    repositories: RepositoryBundle = Depends(get_repositories),
    planning_service: ADKPlanningWorkflowService = Depends(get_planning_service),
) -> Itinerary:
    logger.info("generate_itinerary user=%s title=%s brief=%s", current_user.uid, request.title, request.brief)
    preferences = _require_completed_onboarding(current_user.uid, repositories)
    try:
        result = planning_service.generate_itinerary(
            user_id=current_user.uid,
            brief=request.brief,
            preferences=preferences,
        )
    except PlanningWorkflowError as exc:
        logger.exception("generate_itinerary failed for user=%s brief=%s", current_user.uid, request.brief)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    itinerary = result.itinerary.model_copy(
        update={"title": request.title or result.itinerary.title},
        deep=True,
    )
    repositories.itineraries.create(itinerary.id, itinerary)
    for evidence in result.evidence:
        repositories.evidence.create(f"evidence-{uuid4().hex}", evidence)
    for recommendation in result.recommendations:
        repositories.recommendations.create(recommendation.id, recommendation)
    _audit(
        repositories,
        current_user.uid,
        "itinerary.generate",
        "itinerary",
        itinerary.id,
        details=f"agents={','.join(result.agent_names)}",
    )
    return itinerary


@router.get("/itineraries/{itinerary_id}", response_model=Itinerary)
def get_itinerary(
    itinerary_id: str,
    current_user: VerifiedUser = Depends(get_current_user),
    repositories: RepositoryBundle = Depends(get_repositories),
) -> Itinerary:
    logger.info("get_itinerary user=%s itinerary_id=%s", current_user.uid, itinerary_id)
    return _require_owned_itinerary(itinerary_id, current_user.uid, repositories)


@router.post("/itineraries/{itinerary_id}/routes", response_model=RouteSegmentsResponse)
def compute_itinerary_routes(
    itinerary_id: str,
    request: RouteRequest,
    current_user: VerifiedUser = Depends(get_current_user),
    repositories: RepositoryBundle = Depends(get_repositories),
):
    logger.info(
        "compute_itinerary_routes itinerary_id=%s day_index=%s modes=%s",
        itinerary_id, request.day_index, request.modes,
    )
    itinerary = _require_owned_itinerary(itinerary_id, current_user.uid, repositories)

    if request.day_index < 0 or request.day_index >= len(itinerary.days):
        return RouteSegmentsResponse(segments=[], stop_coordinates=[])

    day = itinerary.days[request.day_index]
    client = GoogleMapsClient()
    allowed_modes = {"WALKING", "DRIVING", "TRANSIT", "BICYCLING", "WALK", "DRIVE", "BICYCLE"}
    requested_modes = request.modes or itinerary.brief.preferred_transport_modes or ["WALKING"]
    modes = [mode for mode in requested_modes if mode in allowed_modes]
    if not modes:
        modes = ["WALKING"]

    # Geocode each stop name to get real coordinates
    stop_coords: list[StopCoordinateResult] = []
    for i, stop in enumerate(day.stops):
        try:
            coords = client.geocode(f"{stop.name}, {itinerary.brief.region}")
            if coords is not None:
                stop_coords.append(
                    StopCoordinateResult(
                        index=i,
                        name=stop.name,
                        lat=coords.latitude,
                        lng=coords.longitude,
                    )
                )
            else:
                logger.warning("geocode returned None for stop=%s", stop.name)
        except Exception as exc:
            logger.warning("geocode failed for stop=%s: %s", stop.name, exc)

    # Compute routes between real coordinates for each mode
    segments: list[RouteSegmentSchema] = []
    for i in range(len(stop_coords) - 1):
        origin = Coordinates(latitude=stop_coords[i].lat, longitude=stop_coords[i].lng)
        destination = Coordinates(latitude=stop_coords[i + 1].lat, longitude=stop_coords[i + 1].lng)
        for mode in modes:
            try:
                route = client.compute_route(
                    origin=origin,
                    destination=destination,
                    travel_mode=mode,
                )
                routes_list = route.get("routes", [])
                if not routes_list:
                    continue
                route_data = routes_list[0]
                duration_str = route_data.get("duration", "0s")
                duration_seconds = _parse_duration(duration_str)
                distance_meters = route_data.get("distanceMeters", 0)
                polyline = route_data.get("polyline", {})
                encoded = polyline.get("encodedPolyline", "")
                segments.append(
                    RouteSegmentSchema(
                        from_stop_index=stop_coords[i].index,
                        to_stop_index=stop_coords[i + 1].index,
                        mode=mode,
                        duration_seconds=duration_seconds,
                        distance_meters=distance_meters,
                        encoded_polyline=encoded,
                    )
                )
                break
            except Exception as exc:
                logger.warning("compute_route failed %s->%s mode=%s: %s", i, i + 1, mode, exc)
                continue

    logger.info("computed %d segments for %d stops", len(segments), len(stop_coords))
    return RouteSegmentsResponse(segments=segments, stop_coordinates=stop_coords)


@router.post("/itineraries/{itinerary_id}/chat", response_model=ChatResponse)
def chat_with_itinerary_agent(
    itinerary_id: str,
    request: ChatRequest,
    current_user: VerifiedUser = Depends(get_current_user),
    repositories: RepositoryBundle = Depends(get_repositories),
):
    logger.info("chat_with_itinerary_agent itinerary_id=%s day_index=%s", itinerary_id, request.day_index)
    itinerary = _require_owned_itinerary(itinerary_id, current_user.uid, repositories)
    try:
        from app.services.planning import ChatAgentService

        agent = ChatAgentService()
        result = agent.process_message(
            message=request.message,
            itinerary=itinerary,
            day_index=request.day_index,
        )
    except Exception:
        logger.exception("chat agent failed itinerary_id=%s", itinerary_id)
        return ChatResponse(
            agent_message="Sorry, the chat agent encountered an error.",
            action="rejected",
        )

    action = result.get("action")
    new_stop = result.get("new_stop")
    insert_idx = result.get("insert_before_index")
    agent_message = sanitize_agent_message(
        result.get("agent_message", "")
    )

    if action == "insert_stop" and new_stop is not None and insert_idx is not None:
        itinerary.days[request.day_index].stops.insert(insert_idx, new_stop)
        for order, stop in enumerate(itinerary.days[request.day_index].stops, start=1):
            stop.suggested_order = order
        repositories.itineraries.update(itinerary.id, itinerary)
        _audit(repositories, current_user.uid, "chat.insert_stop", "itinerary", itinerary.id)
        return ChatResponse(
            agent_message=agent_message or "Stop added.",
            action="insert_stop",
            updated_itinerary=itinerary,
        )

    return ChatResponse(
        agent_message=agent_message
        or "I can only help with adding activities to this itinerary.",
        action=action or "rejected",
    )


def _parse_duration(duration_str: str) -> int:
    """Parse a duration string like '123s' or '1h30m' into seconds."""
    import re
    total = 0
    match = re.match(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", duration_str)
    if match:
        hours = int(match.group(1)) if match.group(1) else 0
        minutes = int(match.group(2)) if match.group(2) else 0
        seconds = int(match.group(3)) if match.group(3) else 0
        total = hours * 3600 + minutes * 60 + seconds
    return total


@router.put("/itineraries/{itinerary_id}", response_model=Itinerary)
def update_itinerary(
    itinerary_id: str,
    request: ItineraryUpdateRequest,
    current_user: VerifiedUser = Depends(get_current_user),
    repositories: RepositoryBundle = Depends(get_repositories),
) -> Itinerary:
    logger.info("update_itinerary user=%s itinerary_id=%s title=%s", current_user.uid, itinerary_id, request.title)
    itinerary = _require_owned_itinerary(itinerary_id, current_user.uid, repositories)
    updates: dict[str, object] = {}
    if request.title is not None:
        updates["title"] = request.title
    if request.brief is not None:
        updates["brief"] = request.brief
    if request.days is not None:
        updates["days"] = request.days
    updated = itinerary.model_copy(update=updates, deep=True)
    repositories.itineraries.update(updated.id, updated)
    _audit(repositories, current_user.uid, "itinerary.update", "itinerary", updated.id)
    return updated


@router.delete("/itineraries/{itinerary_id}", response_model=DeleteResponse)
def delete_itinerary(
    itinerary_id: str,
    current_user: VerifiedUser = Depends(get_current_user),
    repositories: RepositoryBundle = Depends(get_repositories),
) -> DeleteResponse:
    logger.info("delete_itinerary user=%s itinerary_id=%s", current_user.uid, itinerary_id)
    itinerary = _require_owned_itinerary(itinerary_id, current_user.uid, repositories)
    ActionGuardrailService().assert_explicit_confirmation(
        AgentActionType.DELETE_ITINERARY,
        confirmed=True,
    )
    repositories.itineraries.delete(itinerary.id)
    _audit(repositories, current_user.uid, "itinerary.delete", "itinerary", itinerary.id)
    return DeleteResponse(deleted=True, itinerary_id=itinerary.id)


@router.post("/itineraries/{itinerary_id}/start", response_model=LifecycleResult)
def start_itinerary(
    itinerary_id: str,
    confirm_replace: bool = Query(default=False),
    current_user: VerifiedUser = Depends(get_current_user),
    repositories: RepositoryBundle = Depends(get_repositories),
) -> LifecycleResult:
    logger.info("start_itinerary user=%s itinerary_id=%s confirm_replace=%s", current_user.uid, itinerary_id, confirm_replace)
    _require_owned_itinerary(itinerary_id, current_user.uid, repositories)
    result = _run_lifecycle(
        ItineraryLifecycleService().start_itinerary(
            repositories.itineraries.find_by_user(current_user.uid),
            itinerary_id,
            confirm_replace=confirm_replace,
        )
    )
    if result.replacement_required:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "replacement_confirmation_required",
                "replaced_itinerary_id": result.replaced_itinerary_id,
            },
        )
    _persist_lifecycle_result(result, repositories)
    _audit(repositories, current_user.uid, "itinerary.start", "itinerary", itinerary_id)
    return result


@router.post("/itineraries/{itinerary_id}/stop", response_model=LifecycleResult)
def stop_itinerary(
    itinerary_id: str,
    current_user: VerifiedUser = Depends(get_current_user),
    repositories: RepositoryBundle = Depends(get_repositories),
) -> LifecycleResult:
    logger.info("stop_itinerary user=%s itinerary_id=%s", current_user.uid, itinerary_id)
    _require_owned_itinerary(itinerary_id, current_user.uid, repositories)
    result = _run_lifecycle(
        ItineraryLifecycleService().stop_itinerary(
            repositories.itineraries.find_by_user(current_user.uid),
            itinerary_id,
        )
    )
    _persist_lifecycle_result(result, repositories)
    _audit(repositories, current_user.uid, "itinerary.stop", "itinerary", itinerary_id)
    return result


@router.post("/itineraries/{itinerary_id}/complete", response_model=LifecycleResult)
def complete_itinerary(
    itinerary_id: str,
    current_user: VerifiedUser = Depends(get_current_user),
    repositories: RepositoryBundle = Depends(get_repositories),
) -> LifecycleResult:
    logger.info("complete_itinerary user=%s itinerary_id=%s", current_user.uid, itinerary_id)
    _require_owned_itinerary(itinerary_id, current_user.uid, repositories)
    result = _run_lifecycle(
        ItineraryLifecycleService().complete_itinerary(
            repositories.itineraries.find_by_user(current_user.uid),
            itinerary_id,
            user_initiated=True,
        )
    )
    _persist_lifecycle_result(result, repositories)
    _audit(repositories, current_user.uid, "itinerary.complete", "itinerary", itinerary_id)
    return result


@router.post("/itineraries/{itinerary_id}/preference-pattern", response_model=TravelPreferences)
def save_itinerary_preference_pattern(
    itinerary_id: str,
    current_user: VerifiedUser = Depends(get_current_user),
    repositories: RepositoryBundle = Depends(get_repositories),
) -> TravelPreferences:
    logger.info("save_itinerary_preference_pattern user=%s itinerary_id=%s", current_user.uid, itinerary_id)
    itinerary = _require_owned_itinerary(itinerary_id, current_user.uid, repositories)
    preferences = repositories.preferences.get_by_user(current_user.uid) or default_preferences(current_user.uid)
    pattern = ItineraryPreferencePattern(
        id=f"pattern-{uuid4().hex}",
        itinerary_id=itinerary.id,
        name=itinerary.title,
        interests=itinerary.brief.style_interests,
        pace=preferences.pace,
        budget_posture=preferences.budget_posture,
        day_rhythm=preferences.day_rhythm,
        constraints=itinerary.brief.constraints,
        source_preference_version=preferences.version,
    )
    updated = PreferenceService().add_itinerary_pattern(
        preferences,
        pattern,
        explicit_user_action=True,
    )
    repositories.preferences.update(current_user.uid, updated)
    _audit(repositories, current_user.uid, "preferences.add_itinerary_pattern", "itinerary", itinerary.id)
    return updated


@router.post("/itineraries/{itinerary_id}/export", response_model=ExportRequestResponse)
def request_itinerary_export(
    itinerary_id: str,
    current_user: VerifiedUser = Depends(get_current_user),
    repositories: RepositoryBundle = Depends(get_repositories),
) -> ExportRequestResponse:
    logger.info("request_itinerary_export user=%s itinerary_id=%s", current_user.uid, itinerary_id)
    itinerary = _require_owned_itinerary(itinerary_id, current_user.uid, repositories)
    ActionGuardrailService().assert_explicit_confirmation(
        AgentActionType.EXPORT_ITINERARY,
        confirmed=True,
    )
    export_request_id = f"export-{uuid4().hex}"
    _audit(repositories, current_user.uid, "itinerary.export.request", "itinerary", itinerary.id)
    return ExportRequestResponse(export_request_id=export_request_id, itinerary_id=itinerary.id)


@router.post(
    "/itineraries/{itinerary_id}/location-events",
    response_model=ActiveEventIngestionResult,
)
def ingest_location_event(
    itinerary_id: str,
    request: LocationEventRequest,
    current_user: VerifiedUser = Depends(get_current_user),
    repositories: RepositoryBundle = Depends(get_repositories),
    active_event_service: ActiveEventWorkflowService = Depends(get_active_event_service),
) -> ActiveEventIngestionResult:
    logger.info(
        "ingest_location_event user=%s itinerary_id=%s lat=%s lng=%s",
        current_user.uid,
        itinerary_id,
        request.location.latitude,
        request.location.longitude,
    )
    itinerary = _require_owned_itinerary(itinerary_id, current_user.uid, repositories)
    event = request.to_location_event(
        event_id=f"loc-{uuid4().hex}",
        itinerary_id=itinerary.id,
        user_id=current_user.uid,
    )
    result = active_event_service.ingest_location_event(
        event=event,
        itinerary=itinerary,
        repositories=_active_repositories(repositories),
    )
    _audit(
        repositories,
        current_user.uid,
        "location_event.ingest",
        "itinerary",
        itinerary.id,
        details=f"published_event_id={result.published_event_id}",
    )
    return result


@router.post(
    "/itineraries/{itinerary_id}/recovery-proposals/{proposal_id}/accept",
    response_model=RecoveryDecisionResponse,
)
def accept_recovery_proposal(
    itinerary_id: str,
    proposal_id: str,
    current_user: VerifiedUser = Depends(get_current_user),
    repositories: RepositoryBundle = Depends(get_repositories),
    active_event_service: ActiveEventWorkflowService = Depends(get_active_event_service),
) -> RecoveryDecisionResponse:
    logger.info("accept_recovery_proposal user=%s itinerary_id=%s proposal_id=%s", current_user.uid, itinerary_id, proposal_id)
    _require_owned_itinerary(itinerary_id, current_user.uid, repositories)
    proposal = _require_owned_recovery_proposal(proposal_id, itinerary_id, current_user.uid, repositories)
    accepted = active_event_service.accept_recovery_proposal(
        proposal=proposal,
        confirmed=True,
        repositories=_active_repositories(repositories),
    )
    _audit(repositories, current_user.uid, "recovery.accept", "recovery_proposal", proposal_id)
    return RecoveryDecisionResponse(proposal_id=accepted.id, status=accepted.status.value)


@router.post(
    "/itineraries/{itinerary_id}/recovery-proposals/{proposal_id}/reject",
    response_model=RecoveryDecisionResponse,
)
def reject_recovery_proposal(
    itinerary_id: str,
    proposal_id: str,
    current_user: VerifiedUser = Depends(get_current_user),
    repositories: RepositoryBundle = Depends(get_repositories),
    active_event_service: ActiveEventWorkflowService = Depends(get_active_event_service),
) -> RecoveryDecisionResponse:
    logger.info("reject_recovery_proposal user=%s itinerary_id=%s proposal_id=%s", current_user.uid, itinerary_id, proposal_id)
    _require_owned_itinerary(itinerary_id, current_user.uid, repositories)
    proposal = _require_owned_recovery_proposal(proposal_id, itinerary_id, current_user.uid, repositories)
    rejected = active_event_service.reject_recovery_proposal(
        proposal=proposal,
        repositories=_active_repositories(repositories),
    )
    _audit(repositories, current_user.uid, "recovery.reject", "recovery_proposal", proposal_id)
    return RecoveryDecisionResponse(proposal_id=rejected.id, status=rejected.status.value)


def _require_completed_onboarding(
    user_id: str,
    repositories: RepositoryBundle,
) -> TravelPreferences:
    preferences = repositories.preferences.get_by_user(user_id)
    if preferences is None or preferences.onboarding_required:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "onboarding_required",
                "message": "Complete onboarding preferences before creating an itinerary.",
            },
        )
    return preferences


def _require_owned_itinerary(
    itinerary_id: str,
    user_id: str,
    repositories: RepositoryBundle,
) -> Itinerary:
    itinerary = repositories.itineraries.get(itinerary_id)
    if itinerary is None or itinerary.user_id != user_id:
        logger.warning("_require_owned_itinerary 404 itinerary_id=%s user_id=%s", itinerary_id, user_id)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Itinerary not found.")
    return itinerary


def _require_owned_recovery_proposal(
    proposal_id: str,
    itinerary_id: str,
    user_id: str,
    repositories: RepositoryBundle,
) -> RecoveryProposal:
    proposal = repositories.recovery_proposals.get(proposal_id)
    if proposal is None or proposal.itinerary_id != itinerary_id or proposal.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recovery proposal not found.")
    return proposal


def _active_repositories(repositories: RepositoryBundle) -> ActiveEventRepositoryBundle:
    return ActiveEventRepositoryBundle(
        preferences=repositories.preferences,
        itineraries=repositories.itineraries,
        dynamic_preferences=repositories.dynamic_preferences,
        recovery_proposals=repositories.recovery_proposals,
    )


def _run_lifecycle(result: LifecycleResult) -> LifecycleResult:
    return result


def _persist_lifecycle_result(
    result: LifecycleResult,
    repositories: RepositoryBundle,
) -> None:
    for itinerary in result.itineraries:
        repositories.itineraries.update(itinerary.id, itinerary)


def _audit(
    repositories: RepositoryBundle,
    user_id: str,
    action: str,
    target_type: str,
    target_id: str,
    details: str = "",
) -> None:
    event = AuditLogEntry(
        event_id=f"audit-{uuid4().hex}",
        user_id=user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        timestamp=utc_timestamp(),
        details=details,
    )
    repositories.audit_logs.create(event.event_id, event)
