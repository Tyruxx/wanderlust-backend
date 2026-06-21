# Agentic Backend Plan And Progress Log

This document is the backend handoff source of truth. Keep it updated after
each implementation step so another agent can resume without rediscovering
context.

## Current Goal

Build a separate backend in `/Users/alan/Documents/Wanderlust_Trip_Flutter/backend`
for the Flutter app in `../smart_travel_itinerary_flutter`.

The backend will use FastAPI plus Google ADK 2.0. It must enforce the product
guardrails in `../specs/` before invoking agents or external tools.

Security development guidance lives in `skills/wanderlust-agentic-security/SKILL.md`
and must be loaded before changing agent workflows, ADK tools, prompts, MCP/tool
integrations, external-source ingestion, secrets, deployment, persistence, or
ACTIVE itinerary event handling.

## Planned Architecture

- Flutter iOS client owns all persistent state (preferences, saved itineraries) locally.
- Backend storage is entirely in-memory (no Firestore, no Pub/Sub, no Secret Manager).
- Google Cloud is only used for external API calls: Gemini/Vertex AI (via ADK) and Google Maps Platform.
- FastAPI receives explicit request context (X-User-Id header) from Flutter.
- Google ADK graph workflows generate and verify itineraries.
- Google ADK ambient/event workflows process ACTIVE itinerary location and deviation events only.
- Location events are logged locally instead of publishing through Pub/Sub.
- API keys are read from `.env` directly.

## Superseded Directions

- **Identity provider**: Firebase Auth removed. No end-user identity-provider requirement. Backend uses `X-User-Id` header only.
- **Firestore/Pub/Sub/Secret Manager**: All removed. Storage is local-only (in-memory dict). Google Cloud services are only used for Gemini/Vertex AI and Google Maps Platform external API calls.

## Functional Completion Bar

- Flutter completes local preference onboarding before the first itinerary generation.
- Flutter sends explicit trip, preference, itinerary, and ACTIVE-event context to FastAPI via `X-User-Id` header.
- All state persists locally on the Flutter device.
- Itinerary generation calls Google ADK/Vertex AI and real Google Maps Platform APIs.
- ACTIVE itinerary events are logged locally and trigger ACTIVE-only backend handling.
- API keys are read from `.env` file.
- Backend runs locally with `uvicorn`; no Cloud Run dependency for development.

## Required Environment Values

Minimum values before real service calls:

- `GOOGLE_API_KEY` — for Gemini API access (or Vertex AI via ADC with `GOOGLE_CLOUD_PROJECT`)
- `GOOGLE_MAPS_BACKEND_API_KEY` — for backend Places, Routes, Geocoding, and Weather calls
- `GOOGLE_MAPS_IOS_API_KEY` — for Maps SDK for iOS / Flutter map UI calls

## Guardrail Checklist

- [x] Local preference onboarding required before first itinerary generation.
- [x] Preferences stored as structured data, not Markdown-only.
- [x] Preference changes increment a version and affect future agent runs.
- [x] Reset preferences erases local preferences and saved itinerary preference patterns, returns to onboarding, and does not delete saved itineraries.
- [x] Only one itinerary may be ACTIVE.
- [x] Starting another itinerary requires explicit replacement.
- [x] INACTIVE and COMPLETED itineraries reject active location/event ingestion.
- [x] Stop and complete halt location, ambient workflows, suggestions, and dynamic behavior updates.
- [x] Agent chat cannot silently activate, stop, delete, export, book, or buy.
- [x] Itinerary recovery proposals require user acceptance before applying.
- [x] Recommendations include explanation/reasoning and source confidence.
- [x] Social sources are discovery signals only, never factual authority.
- [x] Booking, payment, and calls require explicit confirmation.

## Implementation Steps

### Step 1–7: History

Steps 1–7 were completed under earlier architectural decisions that included
Firestore, Pub/Sub, and Secret Manager. Those cloud services have since been
removed in favor of local-only storage. The implementation details of those
steps are retained in git history but no longer reflect current architecture.

### Step 7a: Remove Superseded Identity Scaffolding

Status: Completed.

Removed Firebase Auth, switched to `X-User-Id` header. See git log for details.

### Step 7b: Local-Only Storage (Remove Firestore, Pub/Sub, Secret Manager)

Status: Completed.

Deliverables:

- Replaced `FirestoreRepository` with `LocalRepository` backed by an in-memory dict.
- Replaced `PubSubLocationEventPublisher` with `LocalLocationEventPublisher` that logs events instead of publishing.
- Removed `google-cloud-firestore`, `google-cloud-pubsub`, `google-cloud-secret-manager` from `pyproject.toml`.
- Removed `firestore_database_id` and `pubsub_*_topic` fields from `Settings`.
- Removed `GOOGLE_CLOUD_PROJECT` from `missing_required_values` (no longer required for local runtime).
- Updated `.env.example`, `.env`, and `README.md` to remove Firestore/PubSub/Secret Manager references.
- Updated `AGENTS.md` to remove Secret Manager and Pub/Sub from security-context guidance.
- Removed `Field` import from `settings.py` (no longer used).

Backend now starts with no cloud dependencies except `google-adk` (Gemini/Vertex) and `httpx` (Maps API calls). All persistence is in-memory.

Verification:

- Backend starts with `uvicorn` and responds on `/readyz` without any Google Cloud credentials.
- Ruff passes for `app`, `tests`, `scripts`.
- Tests updated to use `LocalRepository` instead of mocking Firestore.

### Step 8: End-To-End Functional Validation

Status: Pending.

Planned deliverables:

- Complete local preference onboarding in the Flutter app.
- Run the Flutter app against the running backend and verify itinerary generation, lifecycle actions, and location events.
- Verify all state survives backend process restarts only through Flutter's local persistence.
- Produce a handoff runbook with exact commands, required env values, and known limitations.

## Progress Log

- Step 1–7 completed (see git history for details).
- Step 7a completed: Firebase Auth removed, X-User-Id header adopted.
- Step 7b completed: all cloud storage removed, local-only in-memory storage adopted. Google Cloud now only used for Gemini/Vertex AI and Google Maps Platform external API calls.
