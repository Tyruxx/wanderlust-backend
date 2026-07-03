from __future__ import annotations

from enum import Enum
from urllib.parse import quote_plus, urlparse

from pydantic import BaseModel, Field, field_validator

from app.domain.models import Itinerary


class CheckoutProvider(str, Enum):
    OFFICIAL = "official"
    GETYOURGUIDE = "getyourguide"
    VIATOR = "viator"
    KLOOK = "klook"
    PELAGO = "pelago"
    STRIPE_LINK = "stripe_link"


class ProviderPackageSearchRequest(BaseModel):
    query: str | None = Field(default=None, max_length=500)
    itinerary_id: str = Field(min_length=1)
    day_index: int = Field(ge=0)
    stop_index: int = Field(ge=0)
    limit: int = Field(default=5, ge=1, le=20)
    offset: int = Field(default=0, ge=0)


class ProviderPackageCandidate(BaseModel):
    package_id: str
    name: str
    description: str
    provider: CheckoutProvider
    provider_name: str
    checkout_url: str
    source_url: str
    confidence: str = "medium"
    price_summary: str | None = None
    cancellation_summary: str | None = None
    caveats: list[str] = Field(default_factory=list)
    stripe_backed: bool = False

    @field_validator("checkout_url", "source_url")
    @classmethod
    def url_must_be_https(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("provider checkout URLs must be HTTPS")
        return value


class ProviderPackageSearchResponse(BaseModel):
    activity_name: str
    query: str | None = None
    results: list[ProviderPackageCandidate]
    has_more: bool = False
    next_offset: int | None = None
    evidence_note: str


class ProviderCheckoutRequest(BaseModel):
    package_id: str = Field(min_length=1)
    checkout_url: str
    confirmed: bool = False

    @field_validator("checkout_url")
    @classmethod
    def checkout_url_must_be_https(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("checkout URL must be HTTPS")
        return value


class ProviderCheckoutResponse(BaseModel):
    checkout_url: str
    provider_message: str


class StripePaymentGuardrail(BaseModel):
    allowed: bool
    reason: str | None = None


class ProviderCommerceService:
    """Activity-scoped provider checkout discovery and handoff boundary.

    The first implementation returns verified-provider search handoffs scoped to
    the selected activity. It never creates arbitrary third-party Stripe
    Checkout Sessions because Wanderlust cannot sell products it does not own or
    have a seller/Connect/payment-link relationship for.
    """

    def search_packages(
        self,
        request: ProviderPackageSearchRequest,
        *,
        itinerary: Itinerary,
    ) -> ProviderPackageSearchResponse:
        stop = self._require_stop(itinerary, request.day_index, request.stop_index)
        activity_name = stop.name
        search_text = request.query.strip() if request.query else ""
        scoped_query = " ".join(
            part
            for part in [
                activity_name,
                itinerary.brief.region,
                search_text,
                "tickets tours packages",
            ]
            if part
        )
        candidates = self._provider_candidates(activity_name=activity_name, query=scoped_query)
        start = request.offset
        end = start + request.limit
        page = candidates[start:end]
        return ProviderPackageSearchResponse(
            activity_name=activity_name,
            query=request.query,
            results=page,
            has_more=end < len(candidates),
            next_offset=end if end < len(candidates) else None,
            evidence_note=(
                "Results are scoped to the selected activity and route to external "
                "official or authorized provider checkout/search pages. Verify final "
                "price, availability, cancellation terms, and provider identity before paying."
            ),
        )

    def prepare_checkout(self, request: ProviderCheckoutRequest) -> ProviderCheckoutResponse:
        guardrail = self.assert_payment_confirmation(confirmed=request.confirmed)
        if not guardrail.allowed:
            raise ValueError(guardrail.reason or "Checkout requires explicit confirmation.")
        return ProviderCheckoutResponse(
            checkout_url=request.checkout_url,
            provider_message=(
                "Opening the verified external provider checkout. Wanderlust does not "
                "collect card details or store a local payment history for this handoff."
            ),
        )

    def assert_payment_confirmation(self, *, confirmed: bool) -> StripePaymentGuardrail:
        if not confirmed:
            return StripePaymentGuardrail(
                allowed=False,
                reason="External checkout requires explicit user confirmation.",
            )
        return StripePaymentGuardrail(allowed=True)

    @property
    def exposes_secret_to_flutter(self) -> bool:
        return False

    def _require_stop(self, itinerary: Itinerary, day_index: int, stop_index: int):
        if day_index < 0 or day_index >= len(itinerary.days):
            raise ValueError("Day index is out of range.")
        day = itinerary.days[day_index]
        if stop_index < 0 or stop_index >= len(day.stops):
            raise ValueError("Stop index is out of range.")
        return day.stops[stop_index]

    def _provider_candidates(
        self,
        *,
        activity_name: str,
        query: str,
    ) -> list[ProviderPackageCandidate]:
        encoded = quote_plus(query)
        activity_encoded = quote_plus(activity_name)
        templates = [
            (
                CheckoutProvider.OFFICIAL,
                "Official venue search",
                "Official tickets or packages for {activity}.",
                f"https://www.google.com/search?q={encoded}+official+tickets",
                "high",
                "Official source when available",
                "Check official refund and entry-window terms.",
            ),
            (
                CheckoutProvider.GETYOURGUIDE,
                "GetYourGuide",
                "Guided tours, skip-the-line options, and activity packages for {activity}.",
                f"https://www.getyourguide.com/s/?q={activity_encoded}",
                "medium",
                "Provider-listed price",
                "Cancellation terms vary by listing.",
            ),
            (
                CheckoutProvider.VIATOR,
                "Viator",
                "Tours and experience packages related to {activity}.",
                f"https://www.viator.com/searchResults/all?text={activity_encoded}",
                "medium",
                "Provider-listed price",
                "Read operator and refund details before checkout.",
            ),
            (
                CheckoutProvider.KLOOK,
                "Klook",
                "Tickets, passes, transport add-ons, and local packages for {activity}.",
                f"https://www.klook.com/search/result/?query={activity_encoded}",
                "medium",
                "Provider-listed price",
                "Availability and inclusions can differ by market.",
            ),
            (
                CheckoutProvider.PELAGO,
                "Pelago",
                "Curated travel activities and packages for {activity}.",
                f"https://www.pelago.com/en/search/?q={activity_encoded}",
                "medium",
                "Provider-listed price",
                "Confirm final provider and time slot before paying.",
            ),
            (
                CheckoutProvider.STRIPE_LINK,
                "Public Stripe payment links",
                "Search for public Stripe-backed checkout links for {activity}, when available.",
                f"https://www.google.com/search?q={encoded}+site%3Abuy.stripe.com",
                "low",
                "Only if provider publishes a public link",
                "Use only when the provider identity is clearly verified.",
            ),
        ]
        return [
            ProviderPackageCandidate(
                package_id=f"{provider.value}-{index}",
                name=f"{name} for {activity_name}",
                description=description.format(activity=activity_name),
                provider=provider,
                provider_name=name,
                checkout_url=url,
                source_url=url,
                confidence=confidence,
                price_summary=price_summary,
                cancellation_summary=cancellation,
                caveats=[
                    "External checkout; final purchase happens outside Wanderlust.",
                    "Confirm provider identity, final price, and availability before payment.",
                ],
                stripe_backed=provider == CheckoutProvider.STRIPE_LINK,
            )
            for index, (provider, name, description, url, confidence, price_summary, cancellation)
            in enumerate(templates, start=1)
        ]


class StripeCommerceService(ProviderCommerceService):
    """Compatibility alias for older imports while commerce is provider-based."""
