from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.services.actions import ActivityActionDestination


class AskAnythingIntent(str, Enum):
    INFORMATIONAL = "informational"
    BOOKING = "booking"
    PURCHASE = "purchase"


class AskAnythingRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    venue_name: str = Field(min_length=1, max_length=200)


class AskAnythingResponse(BaseModel):
    intent: AskAnythingIntent
    agent_message: str
    suggested_destination: ActivityActionDestination | None = None


class AskAnythingRouter:
    """Routes broad activity questions without performing side effects."""

    _booking_terms = ("book", "booking", "reservation", "reserve", "call")
    _purchase_terms = ("buy", "purchase", "ticket", "pay", "package", "stripe")

    def classify(self, request: AskAnythingRequest) -> AskAnythingResponse:
        message = request.message.lower()
        if any(term in message for term in self._booking_terms):
            return AskAnythingResponse(
                intent=AskAnythingIntent.BOOKING,
                agent_message="This sounds like a venue call or booking request.",
                suggested_destination=ActivityActionDestination.CALL_VENUE,
            )
        if any(term in message for term in self._purchase_terms):
            return AskAnythingResponse(
                intent=AskAnythingIntent.PURCHASE,
                agent_message="This sounds like a purchase request.",
                suggested_destination=ActivityActionDestination.PURCHASE_WITH_STRIPE,
            )
        return AskAnythingResponse(
            intent=AskAnythingIntent.INFORMATIONAL,
            agent_message=f"I can answer informational questions about {request.venue_name}.",
        )
