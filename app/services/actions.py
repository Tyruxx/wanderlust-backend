from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ActivityActionDestination(str, Enum):
    CALL_VENUE = "call_venue"
    BOOK_OR_BUY_PACKAGES = "book_or_buy_packages"
    ASK_AGENT_ANYTHING = "ask_agent_anything"


class ActivityActionContext(BaseModel):
    itinerary_id: str = Field(min_length=1)
    day_index: int = Field(ge=0)
    stop_index: int = Field(ge=0)
    venue_name: str = Field(min_length=1, max_length=200)
    place_id: str | None = Field(default=None, max_length=200)
    route_context: str | None = Field(default=None, max_length=500)


class ActivityActionOption(BaseModel):
    destination: ActivityActionDestination
    label: str
    description: str
    requires_confirmation_before_side_effect: bool = True


class ActivityActionsService:
    """Defines the safe action hub for a single itinerary activity."""

    def options_for_activity(self, context: ActivityActionContext) -> list[ActivityActionOption]:
        venue = context.venue_name
        return [
            ActivityActionOption(
                destination=ActivityActionDestination.CALL_VENUE,
                label="Call the Venue",
                description=f"Book or prepare a manual call for {venue}.",
            ),
            ActivityActionOption(
                destination=ActivityActionDestination.BOOK_OR_BUY_PACKAGES,
                label="Book or Buy Packages",
                description=(
                    "Find activity-scoped tickets, packages, or products from verified providers."
                ),
            ),
            ActivityActionOption(
                destination=ActivityActionDestination.ASK_AGENT_ANYTHING,
                label="Ask Agent Anything",
                description=f"Ask informational questions about {venue} or this itinerary stop.",
                requires_confirmation_before_side_effect=False,
            ),
        ]
