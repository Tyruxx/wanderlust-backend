from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.settings import get_settings


settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Agentic travel itinerary backend using FastAPI and Google ADK 2.0.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz", tags=["health"])
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name, "env": settings.app_env}


@app.get("/readyz", tags=["health"])
def readyz() -> dict[str, object]:
    missing = settings.missing_required_values
    return {
        "ready": not missing,
        "missing_required_values": missing,
        "guardrail_mode": "planning_scaffold",
    }
