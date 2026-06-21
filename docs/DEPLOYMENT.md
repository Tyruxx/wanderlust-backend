# Deployment Runbook

## Backend

1. Configure local environment variables:

```bash
export GOOGLE_CLOUD_PROJECT="your-project-id"
export GOOGLE_CLOUD_REGION="asia-southeast1"
export CLOUD_RUN_SERVICE="wanderlust-backend"
export CLOUD_RUN_SERVICE_ACCOUNT="wanderlust-backend@your-project-id.iam.gserviceaccount.com"
export FIREBASE_PROJECT_ID="your-firebase-project-id"
export FIREBASE_WEB_API_KEY="your-firebase-web-api-key"
export FIREBASE_IOS_BUNDLE_ID="your.ios.bundle"
export GOOGLE_IOS_CLIENT_ID="your-ios-client-id"
export GOOGLE_IOS_REVERSED_CLIENT_ID="your-reversed-ios-client-id"
export GOOGLE_SERVER_CLIENT_ID="your-server-client-id"
```

2. Enable APIs, create Pub/Sub topics, and grant IAM:

```bash
./scripts/setup_gcp_resources.sh
```

3. Add the backend Maps key to Secret Manager:

```bash
printf '%s' 'YOUR_MAPS_BACKEND_KEY' \
  | gcloud secrets versions add google-maps-backend-api-key \
    --data-file=- \
    --project "$GOOGLE_CLOUD_PROJECT"
```

4. Deploy Cloud Run:

```bash
./scripts/deploy_cloud_run.sh
```

5. Verify:

```bash
curl "$BACKEND_BASE_URL/healthz"
curl "$BACKEND_BASE_URL/readyz"
RUN_REAL_INTEGRATION=1 python scripts/smoke_planning_integration.py
RUN_REAL_INTEGRATION=1 python scripts/smoke_active_event_pubsub.py
```

## Flutter iOS

Build or run the app with backend and iOS Maps configuration:

```bash
flutter run \
  --dart-define=BACKEND_BASE_URL="$BACKEND_BASE_URL" \
  --dart-define=GOOGLE_MAPS_IOS_API_KEY="$GOOGLE_MAPS_IOS_API_KEY"
```

The Flutter app must request a Firebase ID token after Google sign-in and pass it
as a bearer token to every backend API call. The iOS Maps key stays client-side;
the backend Maps key stays in Secret Manager.

For local backend-contract testing before real Firebase Sign-In is wired in the
Flutter client, pass a short-lived test Firebase ID token:

```bash
flutter run \
  --dart-define=BACKEND_BASE_URL="$BACKEND_BASE_URL" \
  --dart-define=GOOGLE_MAPS_IOS_API_KEY="$GOOGLE_MAPS_IOS_API_KEY" \
  --dart-define=FIREBASE_ID_TOKEN_FOR_DEBUG="$FIREBASE_ID_TOKEN"
```

Do not commit this token or put it in checked-in environment files.

## CI Checks

```bash
python -m unittest discover -s tests
ruff check app tests scripts
python scripts/smoke_planning_integration.py
python scripts/smoke_active_event_pubsub.py
```

## Rollback

List revisions and shift traffic back:

```bash
gcloud run revisions list --service "$CLOUD_RUN_SERVICE" --region "$GOOGLE_CLOUD_REGION"
gcloud run services update-traffic "$CLOUD_RUN_SERVICE" \
  --region "$GOOGLE_CLOUD_REGION" \
  --to-revisions "REVISION_NAME=100"
```
