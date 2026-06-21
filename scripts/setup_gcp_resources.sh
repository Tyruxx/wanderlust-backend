#!/usr/bin/env bash
set -euo pipefail

: "${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT}"
: "${GOOGLE_CLOUD_REGION:=asia-southeast1}"
: "${CLOUD_RUN_SERVICE_ACCOUNT:?Set CLOUD_RUN_SERVICE_ACCOUNT, e.g. wanderlust-backend@PROJECT_ID.iam.gserviceaccount.com}"

gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  firestore.googleapis.com \
  pubsub.googleapis.com \
  secretmanager.googleapis.com \
  aiplatform.googleapis.com \
  places.googleapis.com \
  routes.googleapis.com \
  geocoding-backend.googleapis.com \
  weather.googleapis.com \
  --project "${GOOGLE_CLOUD_PROJECT}"

gcloud pubsub topics create location-events --project "${GOOGLE_CLOUD_PROJECT}" || true
gcloud pubsub topics create agent-runs --project "${GOOGLE_CLOUD_PROJECT}" || true
gcloud pubsub topics create notifications --project "${GOOGLE_CLOUD_PROJECT}" || true

for role in \
  roles/datastore.user \
  roles/aiplatform.user \
  roles/pubsub.publisher \
  roles/pubsub.subscriber \
  roles/secretmanager.secretAccessor \
  roles/logging.logWriter \
  roles/monitoring.metricWriter
do
  gcloud projects add-iam-policy-binding "${GOOGLE_CLOUD_PROJECT}" \
    --member "serviceAccount:${CLOUD_RUN_SERVICE_ACCOUNT}" \
    --role "${role}" \
    --quiet
done

if ! gcloud secrets describe google-maps-backend-api-key --project "${GOOGLE_CLOUD_PROJECT}" >/dev/null 2>&1; then
  gcloud secrets create google-maps-backend-api-key \
    --project "${GOOGLE_CLOUD_PROJECT}" \
    --replication-policy automatic
fi

echo "Resource setup complete. Add the Maps key with:"
echo "printf '%s' 'YOUR_MAPS_BACKEND_KEY' | gcloud secrets versions add google-maps-backend-api-key --data-file=- --project '${GOOGLE_CLOUD_PROJECT}'"
