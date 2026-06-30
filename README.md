# Wanderlust Trip Backend

FastAPI backend for the Flutter app in `../smart_travel_itinerary_flutter`.

Backend uses Google ADK 2.0 for agentic planning and active-itinerary workflows.
Google Cloud is only required for external API calls (Gemini/Vertex AI, Gemini
Google Search grounding, and Google Maps Platform).
Backend runtime state is local SQLite only — no Firestore, no Pub/Sub, no
Secret Manager. Flutter remains the local-first source of truth for traveler
preferences and saved itineraries; backend requests use an `X-User-Id` device
context header rather than an end-user identity provider. In the Flutter app
this is an anonymous `anon_...` device ID generated once and stored locally.

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
booking details or `2` to mark the request as received. Calls that end before
`2` is pressed resolve as failed so the user is not told a booking was received
without venue confirmation. On Twilio trial accounts, the test
destination number must be verified in Twilio. The opt-in flag must be set in
the shell for that command so normal `pytest` runs cannot accidentally place a
call just because `.env` exists.

## Google Cloud Run Deployment

Cloud Run is required for the agent-assisted booking call feature because
Twilio must reach public HTTPS and WSS endpoints. The mobile app can stay
anonymous and local-first: it sends the local `anon_...` device ID as
`X-User-Id`, while Cloud Run routes booking-call state by that anonymous key.

### 1. Create Secret Manager Values

The deployment expects these Secret Manager secret names:

```sh
printf '%s' "$GOOGLE_API_KEY" | gcloud secrets create google-api-key --data-file=-
printf '%s' "$GOOGLE_MAPS_BACKEND_API_KEY" | gcloud secrets create google-maps-backend-api-key --data-file=-
printf '%s' "$TWILIO_ACCOUNT_SID" | gcloud secrets create twilio-account-sid --data-file=-
printf '%s' "$TWILIO_AUTH_TOKEN" | gcloud secrets create twilio-auth-token --data-file=-
printf '%s' "$TWILIO_FROM_NUMBER" | gcloud secrets create twilio-from-number --data-file=-
```

If a secret already exists, add a new version instead:

```sh
printf '%s' "$TWILIO_AUTH_TOKEN" | gcloud secrets versions add twilio-auth-token --data-file=-
```

### 2. Deploy With Script

```sh
cd backend
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

### 3. Deploy With Terraform

```sh
cd backend
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

### 4. Build/Run Flutter Against Cloud Run

```sh
flutter run \
  --dart-define=BACKEND_BASE_URL=https://YOUR_CLOUD_RUN_URL \
  --dart-define=GOOGLE_MAPS_IOS_API_KEY=YOUR_IOS_MAPS_KEY
```

### 5. Verify Twilio E2E

Use a Twilio-verified destination number for trial accounts:

```sh
WANDERLUST_RUN_TWILIO_E2E=1 \
WANDERLUST_TWILIO_E2E_TO_NUMBER=+15551234567 \
.venv/bin/python -m pytest tests/test_twilio_e2e.py
```

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
