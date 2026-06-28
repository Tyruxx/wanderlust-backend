#!/usr/bin/env bash
set -euo pipefail

: "${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT}"
: "${GOOGLE_CLOUD_REGION:=asia-southeast1}"
: "${ARTIFACT_REGISTRY_REPOSITORY:=wanderlust}"
: "${CLOUD_RUN_SERVICE:=wanderlust-backend}"
: "${CLOUD_RUN_SERVICE_ACCOUNT:=wanderlust-backend}"

IMAGE="${GOOGLE_CLOUD_REGION}-docker.pkg.dev/${GOOGLE_CLOUD_PROJECT}/${ARTIFACT_REGISTRY_REPOSITORY}/${CLOUD_RUN_SERVICE}:$(git rev-parse --short HEAD)"
SERVICE_ACCOUNT_EMAIL="${CLOUD_RUN_SERVICE_ACCOUNT}@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"
PUBLIC_BASE="${PUBLIC_BACKEND_BASE_URL:-https://pending-cloud-run-url.invalid}"

gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  --project "${GOOGLE_CLOUD_PROJECT}"

if ! gcloud artifacts repositories describe "${ARTIFACT_REGISTRY_REPOSITORY}" \
  --project "${GOOGLE_CLOUD_PROJECT}" \
  --location "${GOOGLE_CLOUD_REGION}" >/dev/null 2>&1; then
  gcloud artifacts repositories create "${ARTIFACT_REGISTRY_REPOSITORY}" \
    --project "${GOOGLE_CLOUD_PROJECT}" \
    --location "${GOOGLE_CLOUD_REGION}" \
    --repository-format docker
fi

if ! gcloud iam service-accounts describe "${SERVICE_ACCOUNT_EMAIL}" \
  --project "${GOOGLE_CLOUD_PROJECT}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${CLOUD_RUN_SERVICE_ACCOUNT}" \
    --project "${GOOGLE_CLOUD_PROJECT}" \
    --display-name "Wanderlust Backend"
fi

for SECRET_NAME in \
  google-api-key \
  google-maps-backend-api-key \
  twilio-account-sid \
  twilio-auth-token \
  twilio-from-number; do
  if ! gcloud secrets describe "${SECRET_NAME}" \
    --project "${GOOGLE_CLOUD_PROJECT}" >/dev/null 2>&1; then
    printf 'Missing Secret Manager secret: %s\n' "${SECRET_NAME}" >&2
    printf 'Create it first, for example: printf %%s "$VALUE" | gcloud secrets create %s --data-file=-\n' "${SECRET_NAME}" >&2
    exit 1
  fi
  gcloud secrets add-iam-policy-binding "${SECRET_NAME}" \
    --project "${GOOGLE_CLOUD_PROJECT}" \
    --member "serviceAccount:${SERVICE_ACCOUNT_EMAIL}" \
    --role roles/secretmanager.secretAccessor \
    >/dev/null
done

gcloud builds submit --project "${GOOGLE_CLOUD_PROJECT}" --tag "${IMAGE}" .

gcloud run deploy "${CLOUD_RUN_SERVICE}" \
  --project "${GOOGLE_CLOUD_PROJECT}" \
  --region "${GOOGLE_CLOUD_REGION}" \
  --image "${IMAGE}" \
  --service-account "${SERVICE_ACCOUNT_EMAIL}" \
  --allow-unauthenticated \
  --timeout 3600 \
  --min-instances 1 \
  --max-instances 1 \
  --set-env-vars "APP_ENV=production,APP_NAME=Wanderlust Trip Backend,BACKEND_HOST=0.0.0.0,BACKEND_PORT=8080,BACKEND_BASE_URL=${PUBLIC_BASE},FRONTEND_BASE_URL=${FRONTEND_BASE_URL:-},CORS_ALLOWED_ORIGINS=${CORS_ALLOWED_ORIGINS:-*},USE_VERTEX_AI=${USE_VERTEX_AI:-false},VERTEX_AI_LOCATION=${VERTEX_AI_LOCATION:-${GOOGLE_CLOUD_REGION}},GEMINI_MODEL=${GEMINI_MODEL:-gemini-2.5-flash},GEMINI_LIVE_MODEL=${GEMINI_LIVE_MODEL:-gemini-3.1-flash-live-preview},BOOKING_CALL_MAX_SECONDS=${BOOKING_CALL_MAX_SECONDS:-300},PUBLIC_BACKEND_BASE_URL=${PUBLIC_BASE}" \
  --set-secrets "GOOGLE_API_KEY=google-api-key:latest,GOOGLE_MAPS_BACKEND_API_KEY=google-maps-backend-api-key:latest,TWILIO_ACCOUNT_SID=twilio-account-sid:latest,TWILIO_AUTH_TOKEN=twilio-auth-token:latest,TWILIO_FROM_NUMBER=twilio-from-number:latest"

SERVICE_URL="$(gcloud run services describe "${CLOUD_RUN_SERVICE}" \
  --project "${GOOGLE_CLOUD_PROJECT}" \
  --region "${GOOGLE_CLOUD_REGION}" \
  --format 'value(status.url)')"

if [[ -z "${PUBLIC_BACKEND_BASE_URL:-}" ]]; then
  gcloud run services update "${CLOUD_RUN_SERVICE}" \
    --project "${GOOGLE_CLOUD_PROJECT}" \
    --region "${GOOGLE_CLOUD_REGION}" \
    --update-env-vars "BACKEND_BASE_URL=${SERVICE_URL},PUBLIC_BACKEND_BASE_URL=${SERVICE_URL}"
fi

printf '%s\n' "${SERVICE_URL}"
