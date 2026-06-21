# Wanderlust Trip Backend

FastAPI backend for the Flutter app in `../smart_travel_itinerary_flutter`.

Backend uses Google ADK 2.0 for agentic planning and active-itinerary workflows.
Google Cloud is only required for external API calls (Gemini/Vertex AI and Google Maps Platform).
All persistent storage is local (in-memory dict — no Firestore, no Pub/Sub, no Secret Manager).

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

- **Gemini / Vertex AI**: set `GOOGLE_API_KEY` in `.env` for Gemini, or configure Vertex AI via ADC.
- **Google Maps Platform**: set `GOOGLE_MAPS_BACKEND_API_KEY` in `.env`.
- **Flutter Maps SDK**: set `GOOGLE_MAPS_IOS_API_KEY` in `.env` and pass via `--dart-define`.

## Guardrails

- Never run active location, ambient agents, or suggestions for INACTIVE or COMPLETED itineraries.
- Never allow more than one ACTIVE itinerary.
- Starting, stopping, completing, deleting, exporting, booking, and payment require explicit user action.
- Social sources are discovery signals only and must be verified before becoming recommendations.
