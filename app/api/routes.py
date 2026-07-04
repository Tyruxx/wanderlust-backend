import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import Response

from app.api.dependencies import (
    RepositoryBundle,
    get_active_event_service,
    get_booking_service,
    get_current_user,
    get_planning_service,
    get_repositories,
)
from app.api.schemas import (
    AskAnythingActivityRequest,
    AskAnythingActivityResponse,
    ChatRequest,
    ChatResponse,
    BookingCallCreateRequest,
    BookingCallStatusResponse,
    DeleteResponse,
    DirectRouteRequest,
    DirectBookingCallCreateRequest,
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
from app.services.ask_anything import AskAnythingRequest, AskAnythingRouter
from app.services.auth import VerifiedUser
from app.services.booking_calls import BookingCallService, get_booking_call_service
from app.services.guardrails import (
    ActionGuardrailService,
    ItineraryLifecycleService,
    PreferenceService,
)
from app.services.repositories import AuditLogEntry
from app.services.maps import Coordinates, GoogleMapsClient, MapsIntegrationError
from app.services.planning import (
    ADKPlanningWorkflowService,
    PlanningWorkflowError,
    sanitize_agent_message,
)
from app.services.stripe_commerce import (
    ProviderCheckoutRequest,
    ProviderCheckoutResponse,
    ProviderCommerceService,
    ProviderPackageSearchRequest,
    ProviderPackageSearchResponse,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["v1"])


@router.get("/preferences", response_model=TravelPreferences)
def get_preferences(
    current_user: VerifiedUser = Depends(get_current_user),
    repositories: RepositoryBundle = Depends(get_repositories),
) -> TravelPreferences:
    logger.info("get_preferences user=%s", current_user.uid)
    return repositories.preferences.get_by_user(current_user.uid) or default_preferences(
        current_user.uid
    )


@router.put("/preferences", response_model=TravelPreferences)
def update_preferences(
    request: PreferenceUpdateRequest,
    current_user: VerifiedUser = Depends(get_current_user),
    repositories: RepositoryBundle = Depends(get_repositories),
) -> TravelPreferences:
    logger.info("update_preferences user=%s fields=%s", current_user.uid, request.to_update_dict())
    existing = repositories.preferences.get_by_user(current_user.uid) or default_preferences(
        current_user.uid
    )
    try:
        updated = PreferenceService().update_onboarding_preferences(
            existing, **request.to_update_dict()
        )
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
    existing = repositories.preferences.get_by_user(current_user.uid) or default_preferences(
        current_user.uid
    )
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
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Autocomplete service unavailable."
        )
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
    logger.info(
        "generate_itinerary user=%s title=%s brief=%s",
        current_user.uid,
        request.title,
        request.brief,
    )
    preferences = _require_completed_onboarding(current_user.uid, repositories)
    try:
        result = planning_service.generate_itinerary(
            user_id=current_user.uid,
            brief=request.brief,
            preferences=preferences,
        )
    except PlanningWorkflowError as exc:
        logger.exception(
            "generate_itinerary failed for user=%s brief=%s", current_user.uid, request.brief
        )
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
        itinerary_id,
        request.day_index,
        request.modes,
    )
    itinerary = _require_owned_itinerary(itinerary_id, current_user.uid, repositories)

    if request.day_index < 0 or request.day_index >= len(itinerary.days):
        return RouteSegmentsResponse(segments=[], stop_coordinates=[])

    requested_modes = request.modes or itinerary.brief.preferred_transport_modes or ["WALKING"]
    response = _compute_route_segments_for_stops(
        region=itinerary.brief.region,
        stops=[
            {"index": index, "name": stop.name, "lat": None, "lng": None}
            for index, stop in enumerate(itinerary.days[request.day_index].stops)
        ],
        requested_modes=requested_modes,
    )
    logger.info(
        "computed %d segments for %d stops",
        len(response.segments),
        len(response.stop_coordinates),
    )
    return response


@router.post("/routes/compute", response_model=RouteSegmentsResponse)
def compute_direct_routes(
    request: DirectRouteRequest,
    current_user: VerifiedUser = Depends(get_current_user),
) -> RouteSegmentsResponse:
    logger.info(
        "compute_direct_routes user=%s stops=%d modes=%s",
        current_user.uid,
        len(request.stops),
        request.modes,
    )
    return _compute_route_segments_for_stops(
        region=request.region,
        stops=[
            {"index": index, "name": stop.name, "lat": stop.lat, "lng": stop.lng}
            for index, stop in enumerate(request.stops)
        ],
        requested_modes=request.modes or ["WALKING"],
    )


def _compute_route_segments_for_stops(
    *,
    region: str,
    stops: list[dict[str, object]],
    requested_modes: list[str],
) -> RouteSegmentsResponse:
    client = GoogleMapsClient()
    allowed_modes = {"WALKING", "DRIVING", "TRANSIT", "BICYCLING", "WALK", "DRIVE", "BICYCLE"}
    modes = [mode for mode in requested_modes if mode in allowed_modes]
    if not modes:
        modes = ["WALKING"]

    stop_coords: list[StopCoordinateResult] = []
    for stop in stops:
        index = int(stop["index"])
        name = str(stop["name"])
        lat = stop.get("lat")
        lng = stop.get("lng")
        if isinstance(lat, int | float) and isinstance(lng, int | float):
            stop_coords.append(
                StopCoordinateResult(index=index, name=name, lat=float(lat), lng=float(lng))
            )
            continue
        try:
            coords = client.geocode(f"{name}, {region}")
            if coords is not None:
                stop_coords.append(
                    StopCoordinateResult(
                        index=index,
                        name=name,
                        lat=coords.latitude,
                        lng=coords.longitude,
                    )
                )
            else:
                logger.warning("geocode returned None for stop=%s", name)
        except Exception as exc:
            logger.warning("geocode failed for stop=%s: %s", name, exc)

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

    return RouteSegmentsResponse(segments=segments, stop_coordinates=stop_coords)


@router.post("/itineraries/{itinerary_id}/chat", response_model=ChatResponse)
def chat_with_itinerary_agent(
    itinerary_id: str,
    request: ChatRequest,
    current_user: VerifiedUser = Depends(get_current_user),
    repositories: RepositoryBundle = Depends(get_repositories),
):
    logger.info(
        "chat_with_itinerary_agent itinerary_id=%s day_index=%s insert_before_index=%s",
        itinerary_id,
        request.day_index,
        request.insert_before_index,
    )
    itinerary = _require_owned_itinerary(itinerary_id, current_user.uid, repositories)
    try:
        from app.services.planning import ChatAgentService

        agent = ChatAgentService()
        result = agent.process_message(
            message=request.message,
            itinerary=itinerary,
            day_index=request.day_index,
            insert_before_index=request.insert_before_index,
            scope=request.scope,
            target_stop_index=request.target_stop_index,
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
    agent_message = sanitize_agent_message(result.get("agent_message", ""))

    if action == "insert_stop" and new_stop is not None and insert_idx is not None:
        if insert_idx < 0:
            insert_idx = 0
        if insert_idx > len(itinerary.days[request.day_index].stops):
            insert_idx = len(itinerary.days[request.day_index].stops)
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

    if action == "update_timing":
        timing_update = result.get("timing_update", {})
        stop_idx = timing_update.get("target_stop_index")
        time_window = timing_update.get("time_window")
        day = itinerary.days[request.day_index]
        if (
            isinstance(stop_idx, int)
            and 0 <= stop_idx < len(day.stops)
            and isinstance(time_window, str)
        ):
            day.stops[stop_idx].time_window = time_window
            repositories.itineraries.update(itinerary.id, itinerary)
            _audit(repositories, current_user.uid, "chat.update_timing", "itinerary", itinerary.id)
            return ChatResponse(
                agent_message=agent_message or "Timing updated.",
                action="update_timing",
                updated_itinerary=itinerary,
            )

    if action == "update_transport_mode":
        transport_update = result.get("transport_update", {})
        modes = transport_update.get("preferred_transport_modes")
        if isinstance(modes, list):
            itinerary.brief.preferred_transport_modes = [str(mode) for mode in modes]
            repositories.itineraries.update(itinerary.id, itinerary)
            _audit(
                repositories,
                current_user.uid,
                "chat.update_transport_mode",
                "itinerary",
                itinerary.id,
            )
            return ChatResponse(
                agent_message=agent_message or "Transport modes updated.",
                action="update_transport_mode",
                updated_itinerary=itinerary,
            )

    if action == "recommend":
        return ChatResponse(
            agent_message=agent_message or "Here are a few recommendations.",
            action="recommend",
            recommendations=result.get("recommendations", []),
        )

    if action == "propose_rewrite":
        return ChatResponse(
            agent_message=agent_message or "I prepared a rewrite proposal for review.",
            action="propose_rewrite",
            proposal=result.get("proposal"),
        )

    if action in {"booking_call_offer", "booking_info", "booking_rejected"}:
        return ChatResponse(
            agent_message=agent_message
            or "I can help with booking details, but I need explicit confirmation before any call.",
            action=action,
            booking_call_offer=result.get("booking_call_offer"),
            booking_fallback=result.get("booking_fallback"),
        )

    return ChatResponse(
        agent_message=agent_message or "I can only help with adding activities to this itinerary.",
        action=action or "rejected",
    )


@router.post(
    "/itineraries/{itinerary_id}/ask-anything",
    response_model=AskAnythingActivityResponse,
)
def ask_agent_anything(
    itinerary_id: str,
    request: AskAnythingActivityRequest,
    current_user: VerifiedUser = Depends(get_current_user),
    repositories: RepositoryBundle = Depends(get_repositories),
) -> AskAnythingActivityResponse:
    itinerary = _require_owned_itinerary(itinerary_id, current_user.uid, repositories)
    if request.day_index >= len(itinerary.days):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="day_index out of range"
        )
    day = itinerary.days[request.day_index]
    if request.stop_index >= len(day.stops):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="stop_index out of range",
        )
    stop = day.stops[request.stop_index]
    routed = AskAnythingRouter().classify(
        AskAnythingRequest(message=request.message, venue_name=stop.name)
    )
    _audit(repositories, current_user.uid, "ask_anything.classify", "itinerary", itinerary.id)
    return AskAnythingActivityResponse(
        agent_message=routed.agent_message,
        intent=routed.intent.value,
        suggested_destination=(
            routed.suggested_destination.value if routed.suggested_destination else None
        ),
    )


@router.post(
    "/itineraries/{itinerary_id}/commerce/packages/search",
    response_model=ProviderPackageSearchResponse,
)
def search_activity_packages(
    itinerary_id: str,
    request: ProviderPackageSearchRequest,
    current_user: VerifiedUser = Depends(get_current_user),
    repositories: RepositoryBundle = Depends(get_repositories),
) -> ProviderPackageSearchResponse:
    logger.info(
        "search_activity_packages user=%s itinerary_id=%s day=%s stop=%s",
        current_user.uid,
        itinerary_id,
        request.day_index,
        request.stop_index,
    )
    if request.itinerary_id != itinerary_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request itinerary_id must match path itinerary_id.",
        )
    itinerary = _require_owned_itinerary(itinerary_id, current_user.uid, repositories)
    try:
        return ProviderCommerceService().search_packages(request, itinerary=itinerary)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post(
    "/itineraries/{itinerary_id}/commerce/provider-checkout",
    response_model=ProviderCheckoutResponse,
)
def prepare_provider_checkout(
    itinerary_id: str,
    request: ProviderCheckoutRequest,
    current_user: VerifiedUser = Depends(get_current_user),
    repositories: RepositoryBundle = Depends(get_repositories),
) -> ProviderCheckoutResponse:
    logger.info(
        "prepare_provider_checkout user=%s itinerary_id=%s package_id=%s",
        current_user.uid,
        itinerary_id,
        request.package_id,
    )
    _require_owned_itinerary(itinerary_id, current_user.uid, repositories)
    try:
        response = ProviderCommerceService().prepare_checkout(request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    _audit(repositories, current_user.uid, "commerce.provider_checkout", "itinerary", itinerary_id)
    return response


@router.post(
    "/itineraries/{itinerary_id}/booking-calls",
    response_model=BookingCallStatusResponse,
)
def start_booking_call(
    itinerary_id: str,
    request: BookingCallCreateRequest,
    current_user: VerifiedUser = Depends(get_current_user),
    repositories: RepositoryBundle = Depends(get_repositories),
    booking_service: BookingCallService = Depends(get_booking_service),
) -> BookingCallStatusResponse:
    logger.info(
        "start_booking_call user=%s itinerary_id=%s day=%s stop=%s",
        current_user.uid,
        itinerary_id,
        request.day_index,
        request.stop_index,
    )
    itinerary = _require_owned_itinerary(itinerary_id, current_user.uid, repositories)
    try:
        record = booking_service.start_call(
            user_id=current_user.uid,
            itinerary=itinerary,
            day_index=request.day_index,
            stop_index=request.stop_index,
            details=request.details,
            confirmed=request.confirmed,
        )
    except Exception as exc:
        logger.warning("start_booking_call rejected itinerary_id=%s: %s", itinerary_id, exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    _audit(repositories, current_user.uid, "booking_call.start", "itinerary", itinerary.id)
    return BookingCallStatusResponse(call=record)


@router.post("/booking-calls", response_model=BookingCallStatusResponse)
def start_direct_booking_call(
    request: DirectBookingCallCreateRequest,
    current_user: VerifiedUser = Depends(get_current_user),
    booking_service: BookingCallService = Depends(get_booking_service),
) -> BookingCallStatusResponse:
    logger.info(
        "start_direct_booking_call user=%s itinerary_id=%s day=%s stop=%s",
        current_user.uid,
        request.itinerary_id,
        request.day_index,
        request.stop_index,
    )
    try:
        record = booking_service.start_direct_call(
            user_id=current_user.uid,
            itinerary_id=request.itinerary_id,
            day_index=request.day_index,
            stop_index=request.stop_index,
            details=request.details,
            confirmed=request.confirmed,
        )
    except Exception as exc:
        logger.warning(
            "start_direct_booking_call rejected itinerary_id=%s: %s",
            request.itinerary_id,
            exc,
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return BookingCallStatusResponse(call=record)


@router.get("/booking-calls/{call_id}", response_model=BookingCallStatusResponse)
def get_booking_call_status(
    call_id: str,
    current_user: VerifiedUser = Depends(get_current_user),
    booking_service: BookingCallService = Depends(get_booking_service),
) -> BookingCallStatusResponse:
    record = booking_service.get_status(call_id, current_user.uid)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking call not found.")
    return BookingCallStatusResponse(call=record)


@router.post("/booking-calls/twilio-status", include_in_schema=False)
async def twilio_booking_status_callback(
    request: Request,
    booking_service: BookingCallService = Depends(get_booking_service),
) -> dict[str, str]:
    try:
        form = await request.form()
        CallSid = str(form.get("CallSid") or "")
        CallStatus = str(form.get("CallStatus") or "")
    except Exception:
        CallSid = ""
        CallStatus = ""
    if CallSid and CallStatus:
        booking_service.update_twilio_status(call_sid=CallSid, status=CallStatus)
    return {"status": "ok"}


@router.get("/booking-calls/twiml/{stream_token}", include_in_schema=False)
@router.post("/booking-calls/twiml/{stream_token}", include_in_schema=False)
def booking_twiml(
    stream_token: str,
    booking_service: BookingCallService = Depends(get_booking_service),
) -> Response:
    return Response(
        content=booking_service.twiml_for_token(stream_token),
        media_type="application/xml",
    )


@router.post("/booking-calls/voice-menu/{stream_token}", include_in_schema=False)
async def booking_voice_menu(
    stream_token: str,
    request: Request,
    booking_service: BookingCallService = Depends(get_booking_service),
) -> Response:
    try:
        form = await request.form()
        digits = str(form.get("Digits") or "")
    except Exception:
        digits = ""
    return Response(
        content=booking_service.handle_voice_menu_choice(
            stream_token=stream_token,
            digits=digits,
        ),
        media_type="application/xml",
    )


@router.websocket("/booking-calls/stream/{stream_token}")
async def booking_call_stream(
    websocket: WebSocket,
    stream_token: str,
) -> None:
    await get_booking_call_service().bridge_stream(websocket, stream_token)


@router.websocket("/booking-calls/ws/{call_id}")
async def booking_call_ws_status(
    websocket: WebSocket,
    call_id: str,
    booking_service: BookingCallService = Depends(get_booking_service),
) -> None:
    await websocket.accept()
    user_id = (websocket.headers.get("x-user-id") or "").strip()
    if not user_id or not booking_service.subscribe_status(call_id, user_id, websocket):
        await websocket.close(code=1008)
        return
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        booking_service.unsubscribe_status(call_id, websocket)


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
    logger.info(
        "update_itinerary user=%s itinerary_id=%s title=%s",
        current_user.uid,
        itinerary_id,
        request.title,
    )
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
    logger.info(
        "start_itinerary user=%s itinerary_id=%s confirm_replace=%s",
        current_user.uid,
        itinerary_id,
        confirm_replace,
    )
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
    logger.info(
        "save_itinerary_preference_pattern user=%s itinerary_id=%s", current_user.uid, itinerary_id
    )
    itinerary = _require_owned_itinerary(itinerary_id, current_user.uid, repositories)
    preferences = repositories.preferences.get_by_user(current_user.uid) or default_preferences(
        current_user.uid
    )
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
    _audit(
        repositories,
        current_user.uid,
        "preferences.add_itinerary_pattern",
        "itinerary",
        itinerary.id,
    )
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
    logger.info(
        "accept_recovery_proposal user=%s itinerary_id=%s proposal_id=%s",
        current_user.uid,
        itinerary_id,
        proposal_id,
    )
    _require_owned_itinerary(itinerary_id, current_user.uid, repositories)
    proposal = _require_owned_recovery_proposal(
        proposal_id, itinerary_id, current_user.uid, repositories
    )
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
    logger.info(
        "reject_recovery_proposal user=%s itinerary_id=%s proposal_id=%s",
        current_user.uid,
        itinerary_id,
        proposal_id,
    )
    _require_owned_itinerary(itinerary_id, current_user.uid, repositories)
    proposal = _require_owned_recovery_proposal(
        proposal_id, itinerary_id, current_user.uid, repositories
    )
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
        logger.warning(
            "_require_owned_itinerary 404 itinerary_id=%s user_id=%s", itinerary_id, user_id
        )
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Recovery proposal not found."
        )
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
