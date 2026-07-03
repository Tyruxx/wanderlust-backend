from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from enum import Enum
from uuid import uuid4
from xml.sax.saxutils import escape

from fastapi import WebSocket
from pydantic import BaseModel, Field, field_validator

from app.core.settings import get_settings
from app.domain.models import (
    AgentActionType,
    DayPlan,
    Itinerary,
    ItineraryStatus,
    PlaceStop,
    TripBrief,
)
from app.services.call_logs import (
    CallLogEntry,
    CallLogRepository,
    build_call_log_repository,
    redact_user_id,
    safe_write_call_log,
    utc_now as call_log_utc_now,
)
from app.services.guardrails import ActionGuardrailService
from app.services.maps import GoogleMapsClient, MapsIntegrationError

logger = logging.getLogger(__name__)


class BookingCallStatus(str, Enum):
    OFFERED = "offered"
    QUEUED = "queued"
    RINGING = "ringing"
    IN_PROGRESS = "in_progress"
    BOOKED = "booked"
    COMPLETED = "completed"
    FAILED = "failed"
    FALLBACK_REQUIRED = "fallback_required"
    SCRUBBED = "scrubbed"


class BookingDetails(BaseModel):
    venue_name: str
    venue_phone: str | None = None
    reservation_datetime: str
    party_size: int = Field(ge=1, le=30)
    reservation_name: str = Field(min_length=1, max_length=120)
    callback_phone: str = Field(min_length=5, max_length=40)
    special_requests: str | None = Field(default=None, max_length=500)

    @field_validator("callback_phone")
    @classmethod
    def callback_phone_is_reasonable(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("callback_phone is required")
        return _validate_reachable_phone(value, field_name="callback_phone")

    @field_validator("venue_phone")
    @classmethod
    def venue_phone_is_reasonable(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return _validate_reachable_phone(value, field_name="venue_phone")


def _validate_reachable_phone(value: str, *, field_name: str) -> str:
    cleaned = value.strip()
    digits = [char for char in cleaned if char.isdigit()]
    if len(digits) < 5:
        raise ValueError(f"{field_name} must include a reachable phone number")
    return cleaned


class BookingCallOffer(BaseModel):
    offer_id: str
    itinerary_id: str
    day_index: int
    stop_index: int
    venue_name: str
    details: BookingDetails | None = None
    missing_fields: list[str] = Field(default_factory=list)
    fallback_instructions: str
    can_call: bool = False
    reason: str | None = None


class BookingCallRecord(BaseModel):
    call_id: str
    offer_id: str
    itinerary_id: str
    user_id: str
    day_index: int
    stop_index: int
    venue_name: str
    status: BookingCallStatus
    twilio_call_sid: str | None = None
    result_summary: str | None = None
    fallback_instructions: str
    created_at: str
    updated_at: str
    details: BookingDetails | None = None


@dataclass
class _LiveCallContext:
    record: BookingCallRecord
    stream_token: str
    expires_at: datetime
    transcript: list[str] = field(default_factory=list)


class BookingCallService:
    def __init__(
        self,
        *,
        maps_client: GoogleMapsClient | None = None,
        call_log_repository: CallLogRepository | None = None,
    ) -> None:
        self.maps_client = maps_client or GoogleMapsClient()
        self.call_log_repository = call_log_repository or build_call_log_repository()
        self._records: dict[str, BookingCallRecord] = {}
        self._stream_contexts: dict[str, _LiveCallContext] = {}
        self._status_subscribers: dict[str, set[WebSocket]] = {}

    def create_offer(
        self,
        *,
        itinerary: Itinerary,
        day_index: int,
        stop_index: int,
        details: BookingDetails | None,
    ) -> BookingCallOffer:
        stop = _require_stop(itinerary, day_index, stop_index)
        venue_name = details.venue_name if details is not None else stop.name
        missing = _missing_booking_fields(details)
        fallback = _fallback_instructions(venue_name, details)
        if missing:
            return BookingCallOffer(
                offer_id=f"booking-offer-{uuid4().hex}",
                itinerary_id=itinerary.id,
                day_index=day_index,
                stop_index=stop_index,
                venue_name=venue_name,
                details=details,
                missing_fields=missing,
                fallback_instructions=fallback,
                can_call=False,
                reason="Booking details are incomplete.",
            )

        assert details is not None
        venue_phone = details.venue_phone or self._lookup_venue_phone(stop, itinerary.brief.region)
        hydrated = details.model_copy(update={"venue_name": venue_name, "venue_phone": venue_phone})
        if not venue_phone:
            return BookingCallOffer(
                offer_id=f"booking-offer-{uuid4().hex}",
                itinerary_id=itinerary.id,
                day_index=day_index,
                stop_index=stop_index,
                venue_name=venue_name,
                details=hydrated,
                fallback_instructions=_fallback_instructions(venue_name, hydrated),
                can_call=False,
                reason="No venue phone number was found from Google Places.",
            )

        settings = get_settings()
        missing_config = [
            name
            for name, value in {
                "TWILIO_ACCOUNT_SID": settings.twilio_account_sid,
                "TWILIO_AUTH_TOKEN": settings.twilio_auth_token,
                "TWILIO_FROM_NUMBER": settings.twilio_from_number,
                "PUBLIC_BACKEND_BASE_URL": settings.public_backend_base_url,
                "GOOGLE_API_KEY": settings.google_api_key,
            }.items()
            if not value
        ]
        return BookingCallOffer(
            offer_id=f"booking-offer-{uuid4().hex}",
            itinerary_id=itinerary.id,
            day_index=day_index,
            stop_index=stop_index,
            venue_name=venue_name,
            details=hydrated,
            fallback_instructions=_fallback_instructions(venue_name, hydrated),
            can_call=not missing_config,
            reason=(
                None
                if not missing_config
                else "Call service is not configured: " + ", ".join(missing_config)
            ),
        )

    def start_call(
        self,
        *,
        user_id: str,
        itinerary: Itinerary,
        day_index: int,
        stop_index: int,
        details: BookingDetails,
        confirmed: bool,
    ) -> BookingCallRecord:
        ActionGuardrailService().assert_explicit_confirmation(
            AgentActionType.PLACE_CALL,
            confirmed=confirmed,
        )
        offer = self.create_offer(
            itinerary=itinerary,
            day_index=day_index,
            stop_index=stop_index,
            details=details,
        )
        now = _utc_now()
        record = BookingCallRecord(
            call_id=f"booking-call-{uuid4().hex}",
            offer_id=offer.offer_id,
            itinerary_id=itinerary.id,
            user_id=user_id,
            day_index=day_index,
            stop_index=stop_index,
            venue_name=offer.venue_name,
            status=BookingCallStatus.FALLBACK_REQUIRED,
            result_summary=offer.reason,
            fallback_instructions=offer.fallback_instructions,
            created_at=now,
            updated_at=now,
            details=offer.details,
        )
        if not offer.can_call or offer.details is None or not offer.details.venue_phone:
            self._records[record.call_id] = _scrub_record(record)
            self._log_call_record(self._records[record.call_id])
            return self._records[record.call_id]

        settings = get_settings()
        stream_token = secrets.token_urlsafe(32)
        self._stream_contexts[stream_token] = _LiveCallContext(
            record=record,
            stream_token=stream_token,
            expires_at=datetime.now(timezone.utc)
            + timedelta(seconds=max(settings.booking_call_max_seconds, 60) + 120),
        )
        try:
            twilio_call_sid = self._create_twilio_call(
                to_number=offer.details.venue_phone,
                stream_token=stream_token,
            )
        except Exception as exc:
            logger.warning("booking call failed before start call_id=%s: %s", record.call_id, exc)
            record.status = BookingCallStatus.FALLBACK_REQUIRED
            record.result_summary = "The call service could not start. Use the chat instructions."
            self._records[record.call_id] = _scrub_record(record)
            self._stream_contexts.pop(stream_token, None)
            self._log_call_record(self._records[record.call_id])
            return self._records[record.call_id]

        record.status = BookingCallStatus.QUEUED
        record.twilio_call_sid = twilio_call_sid
        record.updated_at = _utc_now()
        self._stream_contexts[stream_token].record = record
        self._records[record.call_id] = record
        self._log_call_record(record)
        return _public_record(record)

    def start_direct_call(
        self,
        *,
        user_id: str,
        itinerary_id: str,
        day_index: int,
        stop_index: int,
        details: BookingDetails,
        confirmed: bool,
    ) -> BookingCallRecord:
        itinerary = _synthetic_itinerary_for_booking(
            user_id=user_id,
            itinerary_id=itinerary_id,
            day_index=day_index,
            stop_index=stop_index,
            details=details,
        )
        return self.start_call(
            user_id=user_id,
            itinerary=itinerary,
            day_index=day_index,
            stop_index=stop_index,
            details=details,
            confirmed=confirmed,
        )

    def get_status(self, call_id: str, user_id: str) -> BookingCallRecord | None:
        record = self._records.get(call_id)
        if record is None or record.user_id != user_id:
            return None
        if record.status in {
            BookingCallStatus.BOOKED,
            BookingCallStatus.COMPLETED,
            BookingCallStatus.FAILED,
            BookingCallStatus.FALLBACK_REQUIRED,
        }:
            scrubbed = _scrub_record(record)
            self._records[call_id] = scrubbed
            return scrubbed
        return _public_record(record)

    def subscribe_status(self, call_id: str, user_id: str, websocket: WebSocket) -> bool:
        record = self._records.get(call_id)
        if record is None or record.user_id != user_id:
            return False
        self._status_subscribers.setdefault(call_id, set()).add(websocket)
        return True

    def unsubscribe_status(self, call_id: str, websocket: WebSocket) -> None:
        subs = self._status_subscribers.get(call_id)
        if subs:
            subs.discard(websocket)
            if not subs:
                self._status_subscribers.pop(call_id, None)

    def _push_status_update(self, call_id: str) -> None:
        record = self._records.get(call_id)
        if record is None:
            return
        message = json.dumps({
            "type": "status_update",
            "status": record.status.value,
            "result_summary": record.result_summary,
            "fallback_instructions": record.fallback_instructions,
        })
        for ws in list(self._status_subscribers.get(call_id, set())):
            try:
                asyncio.ensure_future(ws.send_text(message))
            except Exception:
                self.unsubscribe_status(call_id, ws)

    def twiml_for_token(self, stream_token: str) -> str:
        settings = get_settings()
        public_base = settings.public_backend_base_url.rstrip("/")
        ws_base = public_base.replace("https://", "wss://").replace("http://", "ws://")
        stream_url = f"{ws_base}/v1/booking-calls/stream/{escape(stream_token)}"
        menu_url = f"{public_base}/v1/booking-calls/voice-menu/{escape(stream_token)}"
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            "<Connect>"
            f'<Stream url="{stream_url}" />'
            "</Connect>"
            f"{self._confirmation_menu_twiml(stream_token, action_url=menu_url)}"
            "</Response>"
        )

    def update_twilio_status(self, *, call_sid: str, status: str) -> None:
        for record in self._records.values():
            if record.twilio_call_sid != call_sid:
                continue
            mapped = _map_twilio_status(status, current_status=record.status)
            record.status = mapped
            record.updated_at = _utc_now()
            if mapped in {
                BookingCallStatus.BOOKED,
                BookingCallStatus.COMPLETED,
                BookingCallStatus.FAILED,
                BookingCallStatus.FALLBACK_REQUIRED,
            }:
                record.result_summary = record.result_summary or _terminal_summary(mapped)
                self._records[record.call_id] = _scrub_record(record)
            self._log_call_record(record)
            self._push_status_update(record.call_id)
            return

    def handle_voice_menu_choice(self, *, stream_token: str, digits: str | None) -> str:
        context = self._stream_contexts.get(stream_token)
        if context is None or context.expires_at < datetime.now(timezone.utc):
            return (
                '<?xml version="1.0" encoding="UTF-8"?>'
                "<Response><Say>This booking session has expired. Goodbye.</Say><Hangup /></Response>"
            )
        record = context.record
        public_base = get_settings().public_backend_base_url.rstrip("/")
        menu_url = f"{public_base}/v1/booking-calls/voice-menu/{escape(stream_token)}"
        if digits == "2":
            record.status = BookingCallStatus.BOOKED
            record.result_summary = "The venue confirmed that the booking request was received."
            record.updated_at = _utc_now()
            self._records[record.call_id] = _scrub_record(record)
            self._log_call_record(record)
            self._push_status_update(record.call_id)
            self._stream_contexts.pop(stream_token, None)
            return (
                '<?xml version="1.0" encoding="UTF-8"?>'
                "<Response>"
                "<Say>Thank you. The booking has been marked as received. Goodbye.</Say>"
                "<Hangup />"
                "</Response>"
            )
        if digits == "1":
            return (
                '<?xml version="1.0" encoding="UTF-8"?>'
                "<Response>"
                f"<Say>{escape(_booking_voice_summary(record))}</Say>"
                f"{self._confirmation_menu_twiml(stream_token, action_url=menu_url)}"
                "</Response>"
            )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            "<Say>I did not receive a valid selection.</Say>"
            f"{self._confirmation_menu_twiml(stream_token, action_url=menu_url)}"
            "</Response>"
        )

    async def bridge_stream(self, websocket: WebSocket, stream_token: str) -> None:
        context = self._stream_contexts.get(stream_token)
        if context is None or context.expires_at < datetime.now(timezone.utc):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        context.record.status = BookingCallStatus.IN_PROGRESS
        context.record.updated_at = _utc_now()
        self._records[context.record.call_id] = _public_record(context.record)
        self._push_status_update(context.record.call_id)
        bridge = GeminiLiveTwilioBridge(context)
        try:
            await bridge.run(websocket)
            context.record.status = BookingCallStatus.IN_PROGRESS
            context.record.result_summary = "The booking details were delivered. Waiting for the venue to press 2 to confirm receipt."
        except Exception as exc:
            logger.warning(
                "booking media bridge failed call_id=%s: %s", context.record.call_id, exc
            )
            context.record.status = BookingCallStatus.FALLBACK_REQUIRED
            context.record.result_summary = (
                "The live call bridge failed. Use the chat instructions."
            )
        finally:
            context.record.updated_at = _utc_now()
            self._records[context.record.call_id] = _scrub_record(context.record)
            if context.record.status in {
                BookingCallStatus.BOOKED,
                BookingCallStatus.FAILED,
                BookingCallStatus.FALLBACK_REQUIRED,
            }:
                self._stream_contexts.pop(stream_token, None)
            self._log_call_record(context.record)
            self._push_status_update(context.record.call_id)
            await _safe_close(websocket)

    def _lookup_venue_phone(self, stop: PlaceStop, region: str) -> str | None:
        try:
            return self.maps_client.find_phone_number(f"{stop.name}, {region}")
        except MapsIntegrationError:
            raise
        except Exception:
            return None

    def _create_twilio_call(self, *, to_number: str, stream_token: str) -> str:
        settings = get_settings()
        try:
            from twilio.rest import Client
        except Exception as exc:  # pragma: no cover - depends on optional install.
            raise RuntimeError("Twilio SDK is not installed.") from exc
        client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        public_base = settings.public_backend_base_url.rstrip("/")
        call = client.calls.create(
            to=to_number,
            from_=settings.twilio_from_number,
            url=f"{public_base}/v1/booking-calls/twiml/{stream_token}",
            status_callback=f"{public_base}/v1/booking-calls/twilio-status",
            status_callback_event=["initiated", "ringing", "answered", "completed"],
            status_callback_method="POST",
            record=False,
        )
        return str(call.sid)

    def _log_call_record(self, record: BookingCallRecord) -> None:
        safe_write_call_log(
            self.call_log_repository,
            CallLogEntry(
                call_id=record.call_id,
                itinerary_id=record.itinerary_id,
                user_hash=redact_user_id(record.user_id),
                day_index=record.day_index,
                stop_index=record.stop_index,
                venue_name=record.venue_name,
                status=record.status.value,
                provider_call_sid=record.twilio_call_sid,
                result_summary=record.result_summary,
                occurred_at=call_log_utc_now(),
            ),
        )

    def _confirmation_menu_twiml(self, stream_token: str, *, action_url: str) -> str:
        _ = stream_token
        return (
            f'<Gather numDigits="1" action="{escape(action_url)}" method="POST" timeout="12">'
            "<Say>Press 1 to hear the booking information again. "
            "Press 2 if the booking request has been received.</Say>"
            "</Gather>"
            "<Say>No confirmation was received. Goodbye.</Say>"
            "<Hangup />"
        )


class GeminiLiveTwilioBridge:
    """Minimal Twilio <-> Gemini Live bridge shell.

    The bridge validates stream events and performs audio format conversion helpers.
    In local development, it will gracefully fall back if Gemini Live cannot connect.
    """

    def __init__(self, context: _LiveCallContext) -> None:
        self.context = context

    async def run(self, websocket: WebSocket) -> None:
        settings = get_settings()
        if not settings.google_api_key:
            raise RuntimeError("GOOGLE_API_KEY is required for Gemini Live calls.")
        try:
            from google import genai
            from google.genai import types
        except Exception as exc:  # pragma: no cover - runtime dependency path.
            raise RuntimeError("Gemini Live SDK is unavailable.") from exc

        client = genai.Client(api_key=settings.google_api_key)
        system_instruction = _booking_voice_instruction(self.context.record)
        config = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            system_instruction=types.Content(parts=[types.Part(text=system_instruction)]),
        )
        async with client.aio.live.connect(
            model=settings.gemini_live_model,
            config=config,
        ) as session:
            await session.send_realtime_input(text=system_instruction)
            stream_sid: dict[str, str | None] = {"value": None}
            stop_event = asyncio.Event()

            async def twilio_to_gemini() -> None:
                timeout_at = datetime.now(timezone.utc) + timedelta(
                    seconds=max(settings.booking_call_max_seconds, 30)
                )
                while datetime.now(timezone.utc) < timeout_at and not stop_event.is_set():
                    message = await asyncio.wait_for(websocket.receive_text(), timeout=10)
                    data = json.loads(message)
                    event = data.get("event")
                    if event == "start":
                        stream_sid["value"] = str(data.get("streamSid") or "")
                    elif event == "stop":
                        stop_event.set()
                        return
                    elif event == "media":
                        payload = data.get("media", {}).get("payload", "")
                        pcm16 = _twilio_payload_to_pcm16(payload)
                        await session.send_realtime_input(
                            audio=types.Blob(data=pcm16, mime_type="audio/pcm;rate=16000")
                        )

            async def gemini_to_twilio() -> None:
                async for response in session.receive():
                    if stop_event.is_set():
                        return
                    content = getattr(response, "server_content", None)
                    if content is None:
                        continue
                    model_turn = getattr(content, "model_turn", None)
                    parts = getattr(model_turn, "parts", None) or []
                    for part in parts:
                        inline_data = getattr(part, "inline_data", None)
                        if inline_data is None or not stream_sid["value"]:
                            continue
                        payload = _pcm24_to_twilio_payload(inline_data.data)
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "event": "media",
                                    "streamSid": stream_sid["value"],
                                    "media": {"payload": payload},
                                }
                            )
                        )

            tasks = [
                asyncio.create_task(twilio_to_gemini()),
                asyncio.create_task(gemini_to_twilio()),
            ]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in done:
                task.result()


def get_booking_call_service() -> BookingCallService:
    return _BOOKING_CALL_SERVICE


_BOOKING_CALL_SERVICE = BookingCallService()


def _require_stop(itinerary: Itinerary, day_index: int, stop_index: int) -> PlaceStop:
    if day_index < 0 or day_index >= len(itinerary.days):
        raise ValueError("day_index out of range")
    stops = itinerary.days[day_index].stops
    if stop_index < 0 or stop_index >= len(stops):
        raise ValueError("target_stop_index out of range")
    return stops[stop_index]


def _synthetic_itinerary_for_booking(
    *,
    user_id: str,
    itinerary_id: str,
    day_index: int,
    stop_index: int,
    details: BookingDetails,
) -> Itinerary:
    days: list[DayPlan] = []
    for index in range(day_index + 1):
        stops = [
            PlaceStop(
                id=f"booking-placeholder-stop-{index}-{stop}",
                name="Booking context placeholder",
                suggested_order=stop + 1,
                what_to_do="Placeholder stop for cloud booking-call routing.",
            )
            for stop in range(stop_index + 1)
        ]
        days.append(
            DayPlan(
                day_number=index + 1,
                start_location="Booking context",
                end_location="Booking context",
                start_time=time(hour=9),
                end_time=time(hour=18),
                stops=stops,
            )
        )
    days[day_index].stops[stop_index] = PlaceStop(
        id=f"booking-stop-{day_index}-{stop_index}",
        name=details.venue_name,
        suggested_order=stop_index + 1,
        what_to_do="Booking-call target from explicit app request context.",
    )
    return Itinerary(
        id=itinerary_id,
        user_id=user_id,
        title=f"Booking call for {details.venue_name}",
        status=ItineraryStatus.INACTIVE,
        brief=TripBrief(
            region="",
            description="Stateless cloud booking-call context",
            trip_length_days=max(day_index + 1, 1),
        ),
        preference_version=1,
        days=days,
    )


def _missing_booking_fields(details: BookingDetails | None) -> list[str]:
    if details is None:
        return [
            "reservation_datetime",
            "party_size",
            "reservation_name",
            "callback_phone",
        ]
    missing: list[str] = []
    for field_name in [
        "reservation_datetime",
        "party_size",
        "reservation_name",
        "callback_phone",
    ]:
        value = getattr(details, field_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(field_name)
    return missing


def _fallback_instructions(venue_name: str, details: BookingDetails | None) -> str:
    if details is None:
        return (
            f"To book {venue_name}, contact the venue directly with your preferred date, time, "
            "party size, reservation name, callback phone, and any special requests."
        )
    requests = f" Special requests: {details.special_requests}." if details.special_requests else ""
    callback = f" Give callback number {details.callback_phone}."
    return (
        f"To book {venue_name}, ask for a reservation on {details.reservation_datetime} "
        f"for {details.party_size} under {details.reservation_name}. "
        f"{callback}{requests}"
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scrub_record(record: BookingCallRecord) -> BookingCallRecord:
    return record.model_copy(update={"details": None, "status": record.status}, deep=True)


def _public_record(record: BookingCallRecord) -> BookingCallRecord:
    return record.model_copy(update={"details": None}, deep=True)


def _map_twilio_status(
    status: str,
    *,
    current_status: BookingCallStatus | None = None,
) -> BookingCallStatus:
    normalized = status.lower()
    if current_status == BookingCallStatus.BOOKED:
        return BookingCallStatus.BOOKED
    if normalized in {"queued", "initiated"}:
        return BookingCallStatus.QUEUED
    if normalized == "ringing":
        return BookingCallStatus.RINGING
    if normalized in {"answered", "in-progress"}:
        return BookingCallStatus.IN_PROGRESS
    if normalized == "completed":
        return BookingCallStatus.FAILED
    return BookingCallStatus.FAILED


def _terminal_summary(status: BookingCallStatus) -> str:
    if status == BookingCallStatus.BOOKED:
        return "The venue confirmed that the booking request was received."
    if status == BookingCallStatus.COMPLETED:
        return "The booking call completed. Check chat for details and any follow-up."
    return (
        "The booking call ended before the venue confirmed receipt. "
        "Use the chat instructions to book manually."
    )


def _twilio_payload_to_pcm16(payload: str) -> bytes:
    import audioop

    mulaw = base64.b64decode(payload)
    pcm8 = audioop.ulaw2lin(mulaw, 2)
    pcm16, _ = audioop.ratecv(pcm8, 2, 1, 8000, 16000, None)
    return pcm16


def _pcm24_to_twilio_payload(pcm24: bytes) -> str:
    import audioop

    pcm8, _ = audioop.ratecv(pcm24, 2, 1, 24000, 8000, None)
    mulaw = audioop.lin2ulaw(pcm8, 2)
    return base64.b64encode(mulaw).decode("ascii")


def _booking_voice_instruction(record: BookingCallRecord) -> str:
    details = record.details
    if details is None:
        return (
            "You are an AI booking assistant for Wanderlust Trip. "
            "If details are missing, politely end the call and ask the user to use chat instructions."
        )
    special = f" Special requests: {details.special_requests}." if details.special_requests else ""
    callback = f" Give callback number {details.callback_phone}."
    return (
        "You are an AI booking assistant calling a venue on behalf of a traveler. "
        "Immediately disclose that you are an AI assistant calling for the traveler. "
        f"Request a reservation at {details.venue_name} for {details.reservation_datetime}, "
        f"party of {details.party_size}, under {details.reservation_name}. "
        f"{callback}{special} "
        "Do not provide or request payment card details. Do not purchase anything. "
        "If the venue asks for payment, say the traveler will follow up directly. "
        "After giving the booking details, tell the venue that the automated system will ask them "
        "to press 1 to hear the information again or press 2 if the booking request has been received."
    )


def _booking_voice_summary(record: BookingCallRecord) -> str:
    details = record.details
    if details is None:
        return "The booking details are unavailable."
    special = f" Special requests: {details.special_requests}." if details.special_requests else ""
    callback = f" Callback number {details.callback_phone}."
    return (
        f"Booking request for {details.venue_name}: {details.reservation_datetime}, "
        f"party of {details.party_size}, under {details.reservation_name}. "
        f"{callback}{special}"
    )


async def _safe_close(websocket: WebSocket) -> None:
    try:
        await websocket.close()
    except Exception:
        pass
