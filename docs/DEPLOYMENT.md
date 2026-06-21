# Deployment

The backend runs standalone with no cloud dependencies beyond the external
APIs it calls (Gemini/Vertex AI, Google Maps Platform). All storage is
in-memory — no Firestore, no Pub/Sub, no Secret Manager.

## Local

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Set env vars in `.env` for real service calls: `GOOGLE_API_KEY` (Gemini),
`GOOGLE_MAPS_BACKEND_API_KEY` (Maps).

## Production (Cloud Run — optional)

If you want to deploy for availability beyond localhost:

1. Build and push the Docker image:

```bash
gcloud builds submit --tag gcr.io/$PROJECT_ID/wanderlust-backend
```

2. Deploy to Cloud Run with the API keys as env vars:

```bash
gcloud run deploy wanderlust-backend \
  --image gcr.io/$PROJECT_ID/wanderlust-backend \
  --set-env-vars="GOOGLE_API_KEY=...,GOOGLE_MAPS_BACKEND_API_KEY=..." \
  --allow-unauthenticated
```

3. Point the Flutter app at the deployed URL:

```bash
flutter run --dart-define=BACKEND_BASE_URL="$CLOUD_RUN_URL" \
  --dart-define=GOOGLE_MAPS_IOS_API_KEY="$GOOGLE_MAPS_IOS_API_KEY"
```

## Flutter

```bash
flutter run --dart-define=GOOGLE_MAPS_IOS_API_KEY="$GOOGLE_MAPS_IOS_API_KEY"
```

Defaults to `BACKEND_BASE_URL=http://127.0.0.1:8000`. Override via
`--dart-define=BACKEND_BASE_URL=...` to point at a deployed backend.

## CI Checks

```bash
python -m pytest tests/
ruff check app tests scripts
```
