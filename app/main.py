from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router as v1_router
from app.core.settings import get_settings
from app.services.guardrails import GuardrailViolation


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

app.include_router(v1_router)


@app.exception_handler(GuardrailViolation)
def guardrail_violation_handler(_: Request, exc: GuardrailViolation) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={"detail": {"code": exc.code, "message": str(exc)}},
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
