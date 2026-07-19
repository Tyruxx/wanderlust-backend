# Wanderlust Trip Backend

FastAPI backend for the Flutter app in `../wanderlust-frontend-flutter`.

Backend uses Google ADK 2.0 for agentic planning and active-itinerary workflows.
Production backend features run on Cloud Run and store backend app state in
Firestore, scoped by the anonymous `X-User-Id` device context header. Flutter
does not use an end-user identity provider; it generates an anonymous
`anon_...` device ID once and stores it locally. SQLite remains available for
local development and tests.

## Local Setup

```sh
cd wanderlust-backend
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

## Twilio Booking Call E2E Test

The Twilio e2e test places a real outbound call and may incur Twilio charges.
Run it only with an explicit opt-in and a safe test destination number:

```sh
WANDERLUST_RUN_TWILIO_E2E=1 \
WANDERLUST_TWILIO_E2E_TO_NUMBER=+15551234567 \
.venv/bin/python -m pytest tests/test_twilio_e2e.py
```

The test reads the app's Twilio configuration from `.env`, verifies the public
backend URL responds on `/readyz` and the booking TwiML endpoint, starts a
confirmed booking call through `BookingCallService`, checks Twilio returns a
queued call SID, simulates the venue pressing `2` to confirm the request was
received, then hangs up the call. In production calls, Gemini Live delivers the
booking request first, then Twilio asks the venue to press `1` to repeat the
booking details through Gemini Live, `2` to mark the request as received, or
`3` to decline the reservation. Calls that end before `2` or `3` is pressed
resolve as failed so the user is not told a booking was received without venue
confirmation. On Twilio trial accounts, the test
destination number must be verified in Twilio. The opt-in flag must be set in
the shell for that command so normal `pytest` runs cannot accidentally place a
call just because `.env` exists.

## Google Cloud Run Deployment

Cloud Run is required for production backend features and for the
agent-assisted booking call feature because Twilio must reach public HTTPS and
WSS endpoints. The mobile app stays anonymous: it sends the local `anon_...`
device ID as `X-User-Id`, while Cloud Run scopes Firestore app state and
booking-call state by that anonymous key.

For the full production checklist, see `../PRODUCTION_SETUP.md`.

### 1. Prepare Google Cloud Resources

```sh
cd wanderlust-backend
GOOGLE_CLOUD_PROJECT=your-project-id \
GOOGLE_CLOUD_REGION=asia-southeast1 \
./scripts/setup_gcp_resources.sh
```

The setup script enables required Google APIs, creates the runtime service
account and Artifact Registry repository when missing, creates the default
Firestore database when missing, creates required Secret Manager secrets when
missing, and grants the service account narrow Firestore plus per-secret access.

### 2. Add Secret Manager Values

The deployment expects these Secret Manager secret names:

```sh
printf '%s' "$GOOGLE_API_KEY" | gcloud secrets versions add google-api-key --data-file=-
printf '%s' "$GOOGLE_MAPS_BACKEND_API_KEY" | gcloud secrets versions add google-maps-backend-api-key --data-file=-
printf '%s' "$TWILIO_ACCOUNT_SID" | gcloud secrets versions add twilio-account-sid --data-file=-
printf '%s' "$TWILIO_AUTH_TOKEN" | gcloud secrets versions add twilio-auth-token --data-file=-
printf '%s' "$TWILIO_FROM_NUMBER" | gcloud secrets versions add twilio-from-number --data-file=-
```

### 3. Deploy With Script

```sh
cd wanderlust-backend
GOOGLE_CLOUD_PROJECT=your-project-id \
GOOGLE_CLOUD_REGION=asia-southeast1 \
./scripts/deploy_cloud_run.sh
```

The script enables required APIs, creates Artifact Registry and a runtime
service account when needed, builds the Docker image, deploys Cloud Run, then
sets `PUBLIC_BACKEND_BASE_URL` to the generated Cloud Run URL when you have not
provided a custom domain. The current booking-call bridge keeps live call
session state in process memory, so the deployment pins Cloud Run to one warm
instance. Add external session storage before increasing max instances.

Cloud call logging is enabled by default in Cloud Run with
`CALL_LOG_BACKEND=firestore` and `CALL_LOG_COLLECTION=wanderlust_booking_call_logs`.
Production app-state storage is enabled with
`WANDERLUST_STORAGE_BACKEND=firestore` and
`FIRESTORE_COLLECTION_PREFIX=wanderlust`.
The deploy script grants the runtime service account `roles/datastore.user`.
Call logs must stay redacted: no raw callback phone, reservation name, venue
hotline override, or full transcript should be stored.

### 4. Deploy With Terraform

```sh
cd wanderlust-backend
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_REGION=asia-southeast1
export IMAGE="$GOOGLE_CLOUD_REGION-docker.pkg.dev/$GOOGLE_CLOUD_PROJECT/wanderlust/wanderlust-backend:$(git rev-parse --short HEAD)"

gcloud builds submit --project "$GOOGLE_CLOUD_PROJECT" --tag "$IMAGE" .

cd infra/cloud-run
terraform init
terraform apply \
  -var="project_id=$GOOGLE_CLOUD_PROJECT" \
  -var="region=$GOOGLE_CLOUD_REGION" \
  -var="image=$IMAGE"
```

Terraform outputs `service_url`. Use that URL as Flutter's `BACKEND_BASE_URL`.
If you are not using a custom domain, run a second `terraform apply` with:

```sh
terraform apply \
  -var="project_id=$GOOGLE_CLOUD_PROJECT" \
  -var="region=$GOOGLE_CLOUD_REGION" \
  -var="image=$IMAGE" \
  -var="public_backend_base_url=$(terraform output -raw service_url)"
```

### 5. Build/Run Flutter Against Cloud Run

```sh
flutter run \
  --dart-define=BACKEND_BASE_URL=https://YOUR_CLOUD_RUN_URL \
  --dart-define=GOOGLE_MAPS_IOS_API_KEY=YOUR_IOS_MAPS_KEY
```

For App Store/TestFlight builds, prefer:

```sh
cd ../wanderlust-frontend-flutter
PUBLIC_BACKEND_BASE_URL=https://YOUR_CLOUD_RUN_URL \
GOOGLE_MAPS_IOS_API_KEY=YOUR_IOS_MAPS_KEY \
./scripts/build_ios_app_store.sh
```

### 6. Verify Twilio E2E

Use a Twilio-verified destination number for trial accounts:

```sh
WANDERLUST_RUN_TWILIO_E2E=1 \
WANDERLUST_TWILIO_E2E_TO_NUMBER=+15551234567 \
.venv/bin/python -m pytest tests/test_twilio_e2e.py
```

For a manual Cloud Run smoke check:

```sh
CLOUD_RUN_URL="$(gcloud run services describe wanderlust-backend \
  --project "$GOOGLE_CLOUD_PROJECT" \
  --region "$GOOGLE_CLOUD_REGION" \
  --format 'value(status.url)')"
curl "$CLOUD_RUN_URL/readyz"
```

Twilio webhooks should use the same public base URL:

- TwiML: `$CLOUD_RUN_URL/v1/booking-calls/twiml/{stream_token}`
- Media Stream WSS: `wss://.../v1/booking-calls/stream/{stream_token}`
- Status callback: `$CLOUD_RUN_URL/v1/booking-calls/twilio-status`

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
