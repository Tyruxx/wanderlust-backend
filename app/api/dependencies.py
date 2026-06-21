from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.services.auth import FirebaseAuthService, VerifiedUser
from app.services.repositories import (
    AuditLogRepository,
    ItineraryRepository,
    TravelPreferencesRepository,
    UserRepository,
)


bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class RepositoryBundle:
    users: UserRepository
    preferences: TravelPreferencesRepository
    itineraries: ItineraryRepository
    audit_logs: AuditLogRepository


def get_auth_service() -> FirebaseAuthService:
    return FirebaseAuthService()


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    auth_service: FirebaseAuthService = Depends(get_auth_service),
) -> VerifiedUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token is required.",
        )
    try:
        return auth_service.verify_token(credentials.credentials)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Firebase ID token.",
        ) from exc


def get_repositories() -> RepositoryBundle:
    return RepositoryBundle(
        users=UserRepository(),
        preferences=TravelPreferencesRepository(),
        itineraries=ItineraryRepository(),
        audit_logs=AuditLogRepository(),
    )
