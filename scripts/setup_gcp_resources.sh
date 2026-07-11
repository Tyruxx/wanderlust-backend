#!/usr/bin/env bash
set -euo pipefail

: "${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT}"
: "${GOOGLE_CLOUD_REGION:=asia-southeast1}"
: "${FIRESTORE_DATABASE_LOCATION:=${GOOGLE_CLOUD_REGION}}"
: "${ARTIFACT_REGISTRY_REPOSITORY:=wanderlust}"
: "${CLOUD_RUN_SERVICE_ACCOUNT:=wanderlust-backend}"

SERVICE_ACCOUNT_EMAIL="${CLOUD_RUN_SERVICE_ACCOUNT}@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"

gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  firestore.googleapis.com \
  secretmanager.googleapis.com \
  aiplatform.googleapis.com \
  generativelanguage.googleapis.com \
  places.googleapis.com \
  routes.googleapis.com \
  geocoding-backend.googleapis.com \
  weather.googleapis.com \
  --project "${GOOGLE_CLOUD_PROJECT}"

if ! gcloud iam service-accounts describe "${SERVICE_ACCOUNT_EMAIL}" \
  --project "${GOOGLE_CLOUD_PROJECT}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${CLOUD_RUN_SERVICE_ACCOUNT}" \
    --project "${GOOGLE_CLOUD_PROJECT}" \
    --display-name "Wanderlust Backend"
fi

if ! gcloud artifacts repositories describe "${ARTIFACT_REGISTRY_REPOSITORY}" \
  --project "${GOOGLE_CLOUD_PROJECT}" \
  --location "${GOOGLE_CLOUD_REGION}" >/dev/null 2>&1; then
  gcloud artifacts repositories create "${ARTIFACT_REGISTRY_REPOSITORY}" \
    --project "${GOOGLE_CLOUD_PROJECT}" \
    --location "${GOOGLE_CLOUD_REGION}" \
    --repository-format docker
fi

if ! gcloud firestore databases describe --database="(default)" \
  --project "${GOOGLE_CLOUD_PROJECT}" >/dev/null 2>&1; then
  gcloud firestore databases create \
    --database="(default)" \
    --location="${FIRESTORE_DATABASE_LOCATION}" \
    --project "${GOOGLE_CLOUD_PROJECT}"
fi

gcloud projects add-iam-policy-binding "${GOOGLE_CLOUD_PROJECT}" \
  --member "serviceAccount:${SERVICE_ACCOUNT_EMAIL}" \
  --role roles/datastore.user \
  --quiet >/dev/null

for SECRET_NAME in \
  google-api-key \
  google-maps-backend-api-key \
  twilio-account-sid \
  twilio-auth-token \
  twilio-from-number; do
  if ! gcloud secrets describe "${SECRET_NAME}" \
    --project "${GOOGLE_CLOUD_PROJECT}" >/dev/null 2>&1; then
    gcloud secrets create "${SECRET_NAME}" \
      --project "${GOOGLE_CLOUD_PROJECT}" \
      --replication-policy automatic
  fi

  gcloud secrets add-iam-policy-binding "${SECRET_NAME}" \
    --project "${GOOGLE_CLOUD_PROJECT}" \
    --member "serviceAccount:${SERVICE_ACCOUNT_EMAIL}" \
    --role roles/secretmanager.secretAccessor \
    --quiet >/dev/null
done

cat <<EOF
Production resource setup complete.

Add or rotate secret versions with:
  printf '%s' "\$GOOGLE_API_KEY" | gcloud secrets versions add google-api-key --data-file=- --project '${GOOGLE_CLOUD_PROJECT}'
  printf '%s' "\$GOOGLE_MAPS_BACKEND_API_KEY" | gcloud secrets versions add google-maps-backend-api-key --data-file=- --project '${GOOGLE_CLOUD_PROJECT}'
  printf '%s' "\$TWILIO_ACCOUNT_SID" | gcloud secrets versions add twilio-account-sid --data-file=- --project '${GOOGLE_CLOUD_PROJECT}'
  printf '%s' "\$TWILIO_AUTH_TOKEN" | gcloud secrets versions add twilio-auth-token --data-file=- --project '${GOOGLE_CLOUD_PROJECT}'
  printf '%s' "\$TWILIO_FROM_NUMBER" | gcloud secrets versions add twilio-from-number --data-file=- --project '${GOOGLE_CLOUD_PROJECT}'
EOF
