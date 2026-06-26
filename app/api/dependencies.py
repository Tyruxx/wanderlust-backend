from dataclasses import dataclass

from fastapi import Header, HTTPException, status

from app.services.active_events import ActiveEventWorkflowService, get_active_event_workflow_service
from app.services.auth import VerifiedUser
from app.services.booking_calls import BookingCallService, get_booking_call_service
from app.services.repositories import (
    AuditLogRepository,
    DynamicPreferencesRepository,
    EvidenceRepository,
    ItineraryRepository,
    RecommendationRepository,
    RecoveryProposalRepository,
    TravelPreferencesRepository,
)
from app.services.planning import ADKPlanningWorkflowService, get_planning_workflow_service


@dataclass
class RepositoryBundle:
    preferences: TravelPreferencesRepository
    itineraries: ItineraryRepository
    dynamic_preferences: DynamicPreferencesRepository
    evidence: EvidenceRepository
    recommendations: RecommendationRepository
    recovery_proposals: RecoveryProposalRepository
    audit_logs: AuditLogRepository


def get_current_user(
    x_user_id: str = Header(..., alias="X-User-Id"),
) -> VerifiedUser:
    if not x_user_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-User-Id header is required.",
        )
    return VerifiedUser(uid=x_user_id.strip())


def get_repositories() -> RepositoryBundle:
    return RepositoryBundle(
        preferences=TravelPreferencesRepository(),
        itineraries=ItineraryRepository(),
        dynamic_preferences=DynamicPreferencesRepository(),
        evidence=EvidenceRepository(),
        recommendations=RecommendationRepository(),
        recovery_proposals=RecoveryProposalRepository(),
        audit_logs=AuditLogRepository(),
    )


def get_planning_service() -> ADKPlanningWorkflowService:
    return get_planning_workflow_service()


def get_active_event_service() -> ActiveEventWorkflowService:
    return get_active_event_workflow_service()


def get_booking_service() -> BookingCallService:
    return get_booking_call_service()
