# Agentic Backend Plan And Progress Log

This document is the backend handoff source of truth. Keep it updated after
each implementation step so another agent can resume without rediscovering
context.

## Current Goal

Build a separate backend in `/Users/alan/Documents/Wanderlust_Trip_Flutter/backend`
for the Flutter app in `../smart_travel_itinerary_flutter`.

The backend will use FastAPI plus Google ADK 2.0. It must enforce the product
guardrails in `../specs/` before invoking agents or external tools.

## Planned Architecture

- Flutter iOS client authenticates with Google via Firebase and sends Firebase ID tokens.
- FastAPI verifies auth, owns business guardrails, persists state, and exposes REST APIs.
- Firestore stores users, preferences, itineraries, dynamic preferences, place evidence, and audit logs.
- Google ADK graph workflows generate and verify itineraries.
- Google ADK ambient/event workflows process ACTIVE itinerary location and deviation events only.
- Pub/Sub carries location and agent run events.
- Secret Manager stores API keys.
- Cloud Run hosts the backend service.

## Functional Completion Bar

This plan must end with a working app backed by real external services, not only
mocked interfaces. Test doubles remain allowed for unit tests, but the completed
backend must support the following production path:

- Flutter signs in with Google/Firebase and calls FastAPI with a real Firebase ID token.
- FastAPI verifies the token with Firebase Admin using Application Default Credentials.
- Preferences, itineraries, recommendations, evidence, and audit logs persist in Firestore.
- Itinerary generation calls Google ADK/Vertex AI and real Google Maps Platform APIs.
- ACTIVE itinerary events publish through Pub/Sub and trigger ACTIVE-only backend handling.
- Runtime secrets are read from Secret Manager or Cloud Run environment bindings.
- Cloud Run deployment exposes a reachable backend URL for the Flutter app.
- End-to-end smoke tests prove account onboarding, itinerary creation, start/stop/complete,
  and at least one generated itinerary using real configured services.
- Flutter integration is part of completion: the Flutter app must use the deployed backend URL,
  attach Firebase ID tokens to API requests, send iOS location events only for ACTIVE
  itineraries, call itinerary generation/start/stop/complete endpoints, and display recovery
  proposals without applying them until the user accepts.

## Required Environment Values

The committed placeholder is `.env.example`. Real `.env` files are ignored and
must stay local or be supplied through Secret Manager in deployed environments.
Minimum values before real service calls:

- `GOOGLE_CLOUD_PROJECT`
- `GOOGLE_CLOUD_REGION`
- `FIREBASE_PROJECT_ID`
- `FIREBASE_WEB_API_KEY`
- `FIREBASE_IOS_BUNDLE_ID`
- `GOOGLE_IOS_CLIENT_ID`
- `GOOGLE_IOS_REVERSED_CLIENT_ID`
- `GOOGLE_SERVER_CLIENT_ID` for backend audience checks when needed
- Vertex AI service-account access, or `GOOGLE_API_KEY` for local Gemini fallback
- `GOOGLE_MAPS_BACKEND_API_KEY` for backend Places, Routes, Geocoding, and Weather web-service calls
- `GOOGLE_MAPS_IOS_API_KEY` for Maps SDK for iOS / Flutter map UI calls

Optional later:

- `GOOGLE_ANDROID_CLIENT_ID` if Android is added
- `GOOGLE_WEB_CLIENT_ID` if a web client is added
- TikTok API credentials, only if approved
- Instagram Graph API credentials, only if approved
- Stripe credentials for explicit payment flows

## Guardrail Checklist

- [x] Account onboarding required before first itinerary generation.
- [x] Preferences stored as structured data, not Markdown-only.
- [x] Preference changes increment a version and affect future agent runs.
- [x] Reset preferences redirects to onboarding and does not delete itineraries.
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

### Step 1: Backend Foundation

Status: Completed.

Deliverables:

- Created `backend/`.
- Added placeholder `.env`.
- Added Python project metadata in `pyproject.toml`.
- Added FastAPI app scaffold with `/healthz` and `/readyz`.
- Added settings loader with `.env` support.
- Added this plan/progress handoff document.

Verification:

- Python source compiles.
- Required configuration is discoverable through `/readyz`.
- No external network/service calls are performed in this step.

### Step 1a: iOS Auth Environment Alignment

Status: Completed.

Deliverables:

- Replaced mixed platform auth placeholders with iOS-first Google Sign-In fields.
- Added settings fields for iOS bundle ID, iOS client ID, reversed client ID, and server client ID.
- Documented Android/Web OAuth fields as optional future values.

Verification:

- Python source compiles.
- Auth environment names clearly separate client-side iOS values from backend server audience values.

### Step 1b: Secret Hygiene And History Rewrite

Status: Completed.

Deliverables:

- Added `.env` to `.gitignore`.
- Added sanitized `.env.example` for handoff/setup.
- Rewrote backend git history from a new root commit so previous commits that tracked `.env` are no longer reachable from `main`.

Verification:

- `.env` is ignored by Git.
- `.env.example` contains placeholders only.
- Git log contains only the sanitized root commit.

### Step 1c: Split Google Maps Keys

Status: Completed.

Deliverables:

- Added separate `GOOGLE_MAPS_BACKEND_API_KEY` and `GOOGLE_MAPS_IOS_API_KEY` fields.
- Kept legacy Maps env fields for migration so existing local values still work.
- Added a backend settings fallback that prefers the backend key, then legacy Maps keys.
- Added ignore coverage for local service-account JSON files.

Verification:

- `.env` remains ignored.
- `.env.example` contains placeholder values only.
- Python source compiles.

### Step 2: Domain Models And Guardrail Services

Status: Completed.

Deliverables:

- Added Pydantic domain models for preferences, dynamic behavior preferences, trip briefs, day rules, itineraries, day plans, stops, recommendations, source evidence, agent action types, and service commands.
- Added deterministic lifecycle guardrails for ACTIVE, INACTIVE, and COMPLETED status transitions.
- Added single-active-itinerary enforcement with explicit replacement confirmation.
- Added stop/complete service commands that halt location collection, event ingestion, ambient workflows, active suggestions, and dynamic behavior updates.
- Added preference versioning guardrails for updates, reset, stale workflow detection, and explicit user action when saving itinerary preference patterns.
- Added explicit-confirmation guardrails for agent actions that activate, stop, complete, delete, export, book, buy, place calls, apply recovery, or save itinerary patterns.
- Added recommendation guardrails requiring explanation and rejecting low-confidence social-only recommendations.
- Added standard-library unit tests for lifecycle, preference, action, and recommendation guardrails.

Verification:

- `python3` source syntax compilation passes.
- Bundled project runtime runs `python -m unittest discover -s tests`: 11 tests pass.
- System `/usr/bin/python3` cannot run tests because it lacks project dependency `pydantic`; backend target remains Python 3.11+ per `pyproject.toml`.

### Step 3: Auth And Persistence

Status: Completed.

Deliverables:

- Firebase ID-token verification service in `app/services/auth.py` with `FirebaseAuthService` (production via `firebase-admin`) and `MockFirebaseAuthService` (test double).
- Firestore repository layer in `app/services/repositories.py` with:
  - `FirestoreRepository[T]` generic base with create/get/update/delete/query
  - `UserRepository`, `TravelPreferencesRepository`, `ItineraryRepository`
  - `DynamicPreferencesRepository`, `EvidenceRepository`, `RecommendationRepository`, `AuditLogRepository`
- `UserProfile` and `AuditLogEntry` Pydantic models.
- Unit tests in `tests/test_auth.py` (4 tests) and `tests/test_repositories.py` (10 tests).

Verification:

- All 25 tests pass (`python -m unittest discover -s tests`): 11 guardrail + 4 auth + 10 repository tests.

### Step 4: Itinerary APIs

Status: Completed.

Deliverables:

- REST routes for authenticated preferences, itinerary CRUD, start/stop/complete, delete, export request, and save itinerary preference pattern.
- FastAPI auth dependency that verifies real Firebase ID tokens through `FirebaseAuthService` outside `APP_ENV=test`.
- Firestore-backed route handlers using the Step 3 repositories; mocks are limited to tests.
- Account-onboarding guard on first itinerary generation: users without completed preferences cannot generate itineraries.
- Lifecycle route behavior backed by Step 2 guardrails, including single ACTIVE itinerary and stop/complete service commands.
- API tests with mocked Firebase/Firestore dependency overrides.
- Repository serialization updated to write JSON-safe values to Firestore and to support deterministic preference document IDs by user ID.

Verification:

- Installed backend dependencies into the local Codex Python runtime for real import verification.
- Full unit/API suite passes: `python -m unittest discover -s tests` runs 29 tests.
- Ruff passes for `app` and `tests`.
- API routes are production-wired to Firebase auth and Firestore repositories by default; test doubles are injected only through FastAPI dependency overrides.

### Step 5: ADK Planning Workflow

Status: Completed.

Deliverables:

- Trip intake, place discovery, verification, and planner agents implemented through Google ADK/Vertex AI.
- Graph workflow that converts a `TripBrief` plus structured preferences into persisted `Itinerary` records.
- Real Google Maps Platform tool wrappers for Places, Routes, Geocoding, and Weather using `GOOGLE_MAPS_BACKEND_API_KEY`.
- Source-evidence persistence for Maps, official web/search-grounded evidence, and compliant social-source candidates only when credentials are configured.
- Recommendation validation before persistence: explanation required, confidence required, low-confidence social-only results suppressed or marked exploratory.
- Contract tests with fixed fixtures plus a gated integration smoke command that calls Vertex/ADK and Maps when real credentials are present.
- Added `/v1/itineraries/generate` to run the planning workflow and persist itinerary, source evidence, recommendations, and an audit log.
- Added ADK agent definitions for trip intake, place discovery, verification, and itinerary planning.
- Added Google Maps clients for Places Text Search (New), Geocoding, Routes, and Weather current conditions.
- Added Gemini/Vertex planner client using configured `GEMINI_MODEL`, `USE_VERTEX_AI`, and Google Cloud settings.

Verification:

- Full unit/API/contract suite passes: `python -m unittest discover -s tests` runs 34 tests.
- Ruff passes for `app` and `tests`.
- Maps wrappers are verified with `httpx.MockTransport` against expected endpoint shapes and headers.
- Planning workflow tests verify ADK agent registration, generated itinerary construction, evidence creation, and recommendation guardrail validation.
- Gated real-service smoke command added: `RUN_REAL_INTEGRATION=1 python scripts/smoke_planning_integration.py`.

### Step 6: Active Event Workflow

Status: Completed.

Deliverables:

- Authenticated location-event ingestion route that rejects INACTIVE and COMPLETED itineraries before publishing anything.
- Pub/Sub publisher wired to `PUBSUB_LOCATION_EVENTS_TOPIC` with a local/test fake only under test.
- ACTIVE-only event handler that loads current itinerary, preferences, and dynamic behavior from Firestore before invoking ADK ambient logic.
- Dynamic preference updates persisted in Firestore with versioning and immediate preference-version checks.
- Recovery proposal creation that never mutates the itinerary until the user accepts it through an explicit API action.
- Integration smoke path proving a real Pub/Sub message can be published and processed for an ACTIVE itinerary.
- Added domain contracts for location events, geo points, active-event ingestion results, recovery proposal status, and recovery proposals.
- Added `/v1/itineraries/{id}/location-events` for authenticated Flutter location-event ingestion.
- Added recovery proposal accept/reject routes with explicit acceptance before applying recovery state.
- Added Pub/Sub publisher for real Google Cloud Pub/Sub location events.
- Added gated real-service smoke script: `RUN_REAL_INTEGRATION=1 python scripts/smoke_active_event_pubsub.py`.

Verification:

- Full unit/API/contract suite passes: `python -m unittest discover -s tests` runs 36 tests.
- Ruff passes for `app`, `tests`, and `scripts`.
- API tests verify INACTIVE and COMPLETED itineraries reject location events before publish/update.
- API tests verify ACTIVE location events update dynamic preferences, publish through a fake publisher, create pending recovery proposals, and require explicit accept before status changes.

### Step 7: Deployment

Status: Completed.

Deliverables:

- Added a production Dockerfile for the FastAPI backend.
- Added `.dockerignore` rules that keep `.env`, `.env.*`, service-account JSON files, caches, and build artifacts out of container context.
- Added `scripts/setup_gcp_resources.sh` to enable Cloud Run, Cloud Build, Firestore, Pub/Sub, Secret Manager, Vertex AI, and Google Maps Platform APIs; create Pub/Sub topics; create the Maps backend-key secret; and grant the backend service account least-necessary runtime roles.
- Added `scripts/deploy_cloud_run.sh` to build and deploy Cloud Run with production env vars, the backend service account, and Secret Manager binding for `GOOGLE_MAPS_BACKEND_API_KEY`.
- Added `docs/DEPLOYMENT.md` with setup, deploy, smoke-test, Flutter run, CI, and rollback commands.
- Added Flutter runtime configuration for `BACKEND_BASE_URL`, `GOOGLE_MAPS_IOS_API_KEY`, and a debug Firebase ID-token bridge.
- Added a componentized Flutter `BackendApiClient` boundary and IO implementation for itinerary generation, lifecycle actions, and ACTIVE-only location-event posting.
- Wired the add-itinerary flow to call `/v1/itineraries/generate` when a Firebase token is supplied; otherwise the app keeps the local fallback path for development previews.
- Wired itinerary start, stop, and complete UI actions to the backend lifecycle endpoints when the client is configured.
- Added an app-state method for sending current-location events only when an itinerary is ACTIVE and the backend client is configured.

Verification:

- Backend unit/API/contract suite passes: `python -m unittest discover -s tests` runs 36 tests.
- Ruff passes for `app`, `tests`, and `scripts`.
- Backend Python source and deployment scripts pass syntax checks.
- Flutter source is formatted.
- Flutter analyzer passes.
- Flutter test suite status is recorded in the latest handoff entry.
- Guardrail review against `../specs/` confirms deployment/runtime wiring preserves:
  account onboarding before itinerary generation, single ACTIVE itinerary state, no active event posting for INACTIVE/COMPLETED itineraries, explicit start/stop/complete lifecycle actions, server-side Maps key isolation, social sources as discovery-only, and recovery proposals requiring user acceptance.

### Step 8: End-To-End Functional Validation

Status: Pending.

Planned deliverables:

- Seed or create a real Firebase test user and complete onboarding through the app/backend.
- Run the Flutter app against the deployed backend and verify Firebase OAuth login returns a
  token accepted by FastAPI.
- Generate an itinerary through the real API path using Firestore, ADK/Vertex, and Google Maps Platform.
- Start the itinerary, ingest a representative ACTIVE location event through Pub/Sub, and verify dynamic preference/recovery behavior is persisted without violating guardrails.
- Stop and mark the itinerary completed, verifying active services halt and subsequent active events are rejected.
- Verify the Flutter app reflects backend state for saved itineraries, ACTIVE/INACTIVE/COMPLETED
  status, recovery proposal accept/reject, and preference version changes.
- Produce a handoff runbook with exact commands, required env values, smoke-test evidence, known limitations, and remaining production hardening tasks.

## Progress Log

- Step 1 completed: backend scaffold, `.env`, and handoff plan created.
- Step 2 completed: domain models, deterministic guardrail services, and 11 unit tests added.
- Step 3 completed: Firebase auth service, Firestore repository layer, and 14 new tests (25 total).
- Plan updated after Step 3: remaining steps now require real Firebase, Firestore, Google Maps, ADK/Vertex, Pub/Sub, Secret Manager, Cloud Run, and Flutter integration before the backend is considered complete.
- Step 4 completed: authenticated itinerary/preference REST APIs added with real Firebase/Firestore production wiring and 4 API tests (29 total).
- Step 5 completed: ADK/Gemini planning workflow, real Google Maps wrappers, generation endpoint, gated integration smoke script, and 5 new tests added (34 total).
- Step 6 completed: ACTIVE-only location-event ingestion, Pub/Sub publisher, dynamic preference update, recovery proposal contracts, Flutter integration plan updates, and 2 new API scenarios added (36 total).
- Step 7 completed: Cloud Run deployment assets, Secret Manager/IAM setup script, deployment runbook, and Flutter backend API integration boundary added. Step 8 remains the first real-device/deployed-backend validation step with Google OAuth/Firebase ID tokens and real external services end to end.
