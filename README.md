# Wanderlust Trip Backend

Agentic backend for the Flutter app in `../smart_travel_itinerary_flutter`.

This backend is planned as a FastAPI service using Google ADK 2.0 for agentic
planning and active-itinerary workflows. The first scaffold includes
configuration, health endpoints, and a handoff/progress log.

## Local Setup

```sh
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Fill `.env` with local/project values before connecting to Google services.
The committed `.env` intentionally contains placeholders only.

## Guardrail Baseline

- Never run active location, ambient agents, or suggestions for INACTIVE or COMPLETED itineraries.
- Never allow more than one ACTIVE itinerary.
- Starting an itinerary is the only active mode.
- Preference changes must version and affect future active recommendations.
- Agent changes, booking, payment, export, delete, start, and stop require explicit user action.
- Social sources are discovery signals only and must be verified before becoming recommendations.
