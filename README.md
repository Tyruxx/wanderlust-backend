# Wanderlust Trip Backend

FastAPI and Google ADK backend for the Flutter app in
`../wanderlust-frontend-flutter`.

## Runtime

The backend is a cloud-only service. All application API features run on
Google Cloud Run, and production app state is stored in Firestore under the
anonymous `X-User-Id` device scope. Twilio also requires the public Cloud Run
HTTPS/WSS endpoints for booking calls.

Do not document or depend on a locally running backend. Developers may run
lint and isolated tests locally, but Flutter must connect to the deployed
Cloud Run URL.

The complete deployment, Flutter, and App Store workflow is in the
[parent project README](https://github.com/Tyruxx/wanderlust-project#readme).

## Deploy To Cloud Run

From this directory, load the trusted ignored `.env`, select the project, and
prepare the resources:

```bash
set -a
source .env
set +a

export GOOGLE_CLOUD_PROJECT="YOUR_PROJECT_ID"
export GOOGLE_CLOUD_REGION="asia-southeast1"

./scripts/setup_gcp_resources.sh
```

Add required Secret Manager versions, then deploy the local source through
Cloud Build:

```bash
printf '%s' "$GOOGLE_API_KEY" | gcloud secrets versions add google-api-key --data-file=- --project "$GOOGLE_CLOUD_PROJECT"
printf '%s' "$GOOGLE_MAPS_BACKEND_API_KEY" | gcloud secrets versions add google-maps-backend-api-key --data-file=- --project "$GOOGLE_CLOUD_PROJECT"
printf '%s' "$TWILIO_ACCOUNT_SID" | gcloud secrets versions add twilio-account-sid --data-file=- --project "$GOOGLE_CLOUD_PROJECT"
printf '%s' "$TWILIO_AUTH_TOKEN" | gcloud secrets versions add twilio-auth-token --data-file=- --project "$GOOGLE_CLOUD_PROJECT"
printf '%s' "$TWILIO_FROM_NUMBER" | gcloud secrets versions add twilio-from-number --data-file=- --project "$GOOGLE_CLOUD_PROJECT"

./scripts/deploy_cloud_run.sh
```

Verify the service returned by the deployment script:

```bash
curl "https://YOUR_CLOUD_RUN_SERVICE_URL/readyz"
```

Save the HTTPS service URL as `PUBLIC_BACKEND_BASE_URL`, `BACKEND_BASE_URL`,
and `CALL_SERVICE_BASE_URL` in the ignored `.env` before running or building
Flutter.

## Cloud Services

- Cloud Run hosts every backend API and Twilio/Gemini Live callback.
- Firestore stores device-scoped backend state and redacted call logs.
- Secret Manager supplies Gemini, Maps backend, and Twilio credentials.
- Artifact Registry stores the backend container image.
- The runtime service account receives `roles/datastore.user` and narrow
  per-secret access rather than broad project roles.

The current live-call bridge uses process-local session state, so the provided
deployment keeps one warm Cloud Run instance. Add external session storage
before increasing the maximum instance count.

## Local Verification Only

Local commands are for lint and isolated tests, not for starting the backend
service:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

ruff check app tests scripts
env WANDERLUST_DB=memory python -m pytest
```

The live Twilio test places a real call and may incur charges. Run it only with
an explicit opt-in and a safe verified destination:

```bash
WANDERLUST_RUN_TWILIO_E2E=1 \
WANDERLUST_TWILIO_E2E_TO_NUMBER=+15551234567 \
python -m pytest tests/test_twilio_e2e.py
```

## Guardrails

- Never run active location events, ambient agents, or suggestions for an
  INACTIVE or COMPLETED itinerary.
- Never allow more than one ACTIVE itinerary.
- Calls, purchases, lifecycle changes, deletion, export, and recovery
  application require explicit user action.
- Model and external-source output remains untrusted until typed validation,
  source checks, and deterministic policy gates pass.
- Call logs must remain redacted and must not retain callback phone,
  reservation name, venue hotline overrides, or complete transcripts.
- `.env`, credentials, service-account JSON, and API keys must never be
  committed.
