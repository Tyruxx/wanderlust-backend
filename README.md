# Wanderlust Trip Backend

FastAPI backend for the Flutter app in `../smart_travel_itinerary_flutter`.

Backend uses Google ADK 2.0 for agentic planning and active-itinerary workflows.
Google Cloud is only required for external API calls (Gemini/Vertex AI, Gemini
Google Search grounding, and Google Maps Platform).
Backend runtime state is local SQLite only — no Firestore, no Pub/Sub, no
Secret Manager. Flutter remains the local-first source of truth for traveler
preferences and saved itineraries; backend requests use an `X-User-Id` device
context header rather than an end-user identity provider.

## Local Setup

```sh
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Fill `.env` with your Gemini API key and/or Maps API key before making real service calls.

## Prerequisites for Real Service Calls

- **Gemini / Vertex AI / Google Search grounding**: set `GOOGLE_API_KEY` in `.env` for Gemini and grounded search, or configure Vertex AI via ADC for planner calls.
- **Google Maps Platform**: set `GOOGLE_MAPS_BACKEND_API_KEY` in `.env`.
- **Flutter Maps SDK**: set `GOOGLE_MAPS_IOS_API_KEY` in `.env` and pass via `--dart-define`.
- **Agent-assisted booking calls**: set `TWILIO_ACCOUNT_SID`,
  `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, `PUBLIC_BACKEND_BASE_URL`, and
  `GEMINI_LIVE_MODEL`. `PUBLIC_BACKEND_BASE_URL` must be reachable by Twilio
  over HTTPS/WSS for live calls.

## Guardrails

- Never run active location, ambient agents, or suggestions for INACTIVE or COMPLETED itineraries.
- Never allow more than one ACTIVE itinerary.
- Starting, stopping, completing, deleting, exporting, booking, and payment require explicit user action.
- Booking calls require explicit confirmation, per-request reservation details,
  and must fall back to chat instructions when call infrastructure is missing.
- Reservation name and callback phone are scrubbed from call status records
  after terminal status.
- Social sources are discovery signals only and must be verified before becoming recommendations.
- Google Search grounding output is untrusted evidence until schema validation,
  dedupe, ranking, and recommendation guardrails pass.
