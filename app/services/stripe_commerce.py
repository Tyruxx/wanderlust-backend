from __future__ import annotations

from pydantic import BaseModel, Field


class StripeProductSearchRequest(BaseModel):
    query: str | None = Field(default=None, max_length=500)
    itinerary_id: str = Field(min_length=1)
    day_index: int = Field(ge=0)
    stop_index: int = Field(ge=0)
    limit: int = Field(default=5, ge=1, le=20)
    offset: int = Field(default=0, ge=0)


class StripeProductCandidate(BaseModel):
    product_id: str
    price_id: str | None = None
    name: str
    description: str | None = None
    currency: str | None = None
    unit_amount: int | None = None
    source: str = "stripe"


class StripePaymentGuardrail(BaseModel):
    allowed: bool
    reason: str | None = None


class StripeCommerceService:
    """Boundary for future Stripe-backed commerce agents and payment APIs."""

    def assert_payment_confirmation(self, *, confirmed: bool) -> StripePaymentGuardrail:
        if not confirmed:
            return StripePaymentGuardrail(
                allowed=False,
                reason="Payment requires explicit user confirmation.",
            )
        return StripePaymentGuardrail(allowed=True)

    @property
    def exposes_secret_to_flutter(self) -> bool:
        return False
