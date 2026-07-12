from __future__ import annotations

import json
import re
from enum import Enum
from html import unescape
from typing import Protocol
from urllib.parse import quote_plus, urlparse

import httpx
from google import genai
from pydantic import BaseModel, Field, field_validator

from app.core.settings import get_settings
from app.domain.models import Itinerary
from app.services.agentic.workflows import build_wanderlust_adk_workflows


class CheckoutProvider(str, Enum):
    OFFICIAL = "official"
    GETYOURGUIDE = "getyourguide"
    VIATOR = "viator"
    KLOOK = "klook"
    PELAGO = "pelago"
    TIQETS = "tiqets"
    HEADOUT = "headout"
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
    validation_summary: str | None = None
    source_type: str | None = None
    relevance_rationale: str | None = None
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


class AgenticProviderPackageCandidate(BaseModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    provider_name: str = Field(min_length=1)
    checkout_url: str
    source_url: str
    confidence: str = "medium"
    price_summary: str | None = None
    cancellation_summary: str | None = None
    validation_summary: str | None = None
    source_type: str | None = None
    relevance_rationale: str | None = None
    caveats: list[str] = Field(default_factory=list)
    stripe_backed: bool = False

    @field_validator("checkout_url", "source_url")
    @classmethod
    def url_must_be_https(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("agentic provider URLs must be HTTPS")
        return value


class AgenticProviderPackageOutput(BaseModel):
    candidates: list[AgenticProviderPackageCandidate] = Field(default_factory=list)
    evidence_note: str = (
        "AI-assisted package discovery used grounded web evidence. Verify final "
        "availability, price, provider identity, and cancellation terms externally."
    )


class ProviderPackageSearchClient(Protocol):
    def search(
        self,
        *,
        activity_name: str,
        region: str,
        query: str,
        limit: int,
    ) -> AgenticProviderPackageOutput: ...


class LinkValidationEvidence(BaseModel):
    valid: bool
    summary: str
    source_type: str | None = None
    relevance_rationale: str | None = None
    final_url: str | None = None


class ProviderLinkContentValidator:
    MAX_BYTES = 180_000
    ERROR_TERMS = ("404", "not found", "page not found", "access denied", "forbidden")

    def __init__(self, *, http_client: httpx.Client | None = None) -> None:
        settings = get_settings()
        self.http_client = http_client or httpx.Client(
            timeout=min(settings.request_timeout_seconds, 8),
            follow_redirects=True,
            max_redirects=4,
        )

    def validate(self, *, url: str, activity_name: str, region: str) -> LinkValidationEvidence:
        if _is_disallowed_checkout_url(url):
            return LinkValidationEvidence(valid=False, summary="URL failed deterministic safety checks.")
        try:
            response = self.http_client.get(
                url,
                headers={
                    "User-Agent": "WanderlustTripBot/1.0 (+package-link-validation)",
                    "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1",
                },
            )
        except Exception:
            return LinkValidationEvidence(valid=False, summary="URL could not be reached during validation.")
        final_url = str(response.url)
        if response.status_code >= 400 or _is_disallowed_checkout_url(final_url):
            return LinkValidationEvidence(
                valid=False,
                summary=f"URL returned HTTP {response.status_code} or redirected to a disallowed page.",
                final_url=final_url,
            )
        content_type = response.headers.get("content-type", "").lower()
        if not any(token in content_type for token in ("text/html", "text/plain", "application/xhtml")):
            return LinkValidationEvidence(
                valid=False,
                summary="URL did not return a readable HTML/text page.",
                final_url=final_url,
            )
        raw = response.content[: self.MAX_BYTES]
        text = _page_text_snippet(raw.decode(response.encoding or "utf-8", errors="ignore"))
        lowered = text.lower()
        if any(term in lowered[:2000] for term in self.ERROR_TERMS):
            return LinkValidationEvidence(
                valid=False,
                summary="URL content looks like an error or unavailable page.",
                final_url=final_url,
            )
        activity_terms = _important_terms(activity_name)
        region_terms = _important_terms(region)
        provider_host = urlparse(final_url).netloc.lower()
        activity_matches = [term for term in activity_terms if term in lowered]
        region_matches = [term for term in region_terms if term in lowered]
        officialish = any(term in provider_host for term in ("getyourguide", "viator", "klook", "pelago", "tiqets", "headout", "stripe"))
        if not activity_matches and len(activity_terms) > 0:
            return LinkValidationEvidence(
                valid=False,
                summary="Validated page content did not mention the selected activity.",
                final_url=final_url,
            )
        if not region_matches and not officialish and region_terms:
            return LinkValidationEvidence(
                valid=False,
                summary="Validated page content did not mention the activity region or a known provider.",
                final_url=final_url,
            )
        source_type = "known_provider" if officialish else "official"
        rationale_parts = []
        if activity_matches:
            rationale_parts.append(f"matched activity terms: {', '.join(activity_matches[:4])}")
        if region_matches:
            rationale_parts.append(f"matched region terms: {', '.join(region_matches[:3])}")
        return LinkValidationEvidence(
            valid=True,
            summary="Fetched and checked the page content for reachability and activity relevance.",
            source_type=source_type,
            relevance_rationale="; ".join(rationale_parts) or "Page belongs to a known travel provider and is scoped by the activity query.",
            final_url=final_url,
        )


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

    def __init__(
        self,
        *,
        search_client: ProviderPackageSearchClient | None = None,
        link_validator: ProviderLinkContentValidator | None = None,
    ) -> None:
        self.search_client = search_client or GeminiProviderPackageSearchClient()
        self.link_validator = link_validator or ProviderLinkContentValidator()
        self.workflows = build_wanderlust_adk_workflows(get_settings().gemini_model)

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
        candidates = self._agentic_provider_candidates(
            activity_name=activity_name,
            region=itinerary.brief.region,
            query=scoped_query,
            limit=max(request.limit + request.offset, 5),
        )
        evidence_note = (
            "Results are scoped to the selected activity and were discovered by the "
            f"{self.workflows.package_search_sequence.name} ADK workflow. Verify final "
            "price, availability, cancellation terms, and provider identity before paying."
        )
        if not candidates:
            candidates = self._provider_candidates(activity_name=activity_name, query=scoped_query)
            evidence_note = (
                "AI package search was unavailable or returned no agent-validated checkout "
                "links, so these are scoped provider discovery links. Verify final price, "
                "availability, cancellation terms, and provider identity before paying."
            )
        start = request.offset
        end = start + request.limit
        page = candidates[start:end]
        return ProviderPackageSearchResponse(
            activity_name=activity_name,
            query=request.query,
            results=page,
            has_more=end < len(candidates),
            next_offset=end if end < len(candidates) else None,
            evidence_note=evidence_note,
        )

    def prepare_checkout(self, request: ProviderCheckoutRequest) -> ProviderCheckoutResponse:
        guardrail = self.assert_payment_confirmation(confirmed=request.confirmed)
        if not guardrail.allowed:
            raise ValueError(guardrail.reason or "Checkout requires explicit confirmation.")
        if _is_disallowed_checkout_url(request.checkout_url):
            raise ValueError("Checkout URL is not a valid provider page.")
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
        activity_encoded = quote_plus(activity_name)
        templates = [
            (
                CheckoutProvider.GETYOURGUIDE,
                "GetYourGuide discovery",
                "Search guided tours, skip-the-line options, and activity packages for {activity}.",
                f"https://www.getyourguide.com/s/?q={activity_encoded}",
                "medium",
                "Provider-listed price",
                "Cancellation terms vary by listing.",
            ),
            (
                CheckoutProvider.VIATOR,
                "Viator discovery",
                "Search tours and experience packages related to {activity}.",
                f"https://www.viator.com/searchResults/all?text={activity_encoded}",
                "medium",
                "Provider-listed price",
                "Read operator and refund details before checkout.",
            ),
            (
                CheckoutProvider.KLOOK,
                "Klook discovery",
                "Search tickets, passes, transport add-ons, and local packages for {activity}.",
                f"https://www.klook.com/search/result/?query={activity_encoded}",
                "medium",
                "Provider-listed price",
                "Availability and inclusions can differ by market.",
            ),
            (
                CheckoutProvider.PELAGO,
                "Pelago discovery",
                "Search curated travel activities and packages for {activity}.",
                f"https://www.pelago.com/en/search/?q={activity_encoded}",
                "medium",
                "Provider-listed price",
                "Confirm final provider and time slot before paying.",
            ),
            (
                CheckoutProvider.TIQETS,
                "Tiqets discovery",
                "Search museum, attraction, and experience tickets for {activity}.",
                f"https://www.tiqets.com/en/search/?q={activity_encoded}",
                "medium",
                "Provider-listed price",
                "Confirm official operator, final price, and entry window before paying.",
            ),
            (
                CheckoutProvider.HEADOUT,
                "Headout discovery",
                "Search activity tickets, tours, and experiences for {activity}.",
                f"https://www.headout.com/search/?q={activity_encoded}",
                "medium",
                "Provider-listed price",
                "Confirm final provider, availability, and cancellation rules before paying.",
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
                validation_summary=(
                    "Fallback discovery link only; no agent-validated checkout page was available."
                ),
                source_type="provider_discovery",
                relevance_rationale=f"Search is scoped to {activity_name}.",
                caveats=[
                    "Discovery link; choose a specific verified listing on the provider site.",
                    "Confirm provider identity, final price, and availability before payment.",
                ],
                stripe_backed=provider == CheckoutProvider.STRIPE_LINK,
            )
            for index, (provider, name, description, url, confidence, price_summary, cancellation)
            in enumerate(templates, start=1)
        ]

    def _agentic_provider_candidates(
        self,
        *,
        activity_name: str,
        region: str,
        query: str,
        limit: int,
    ) -> list[ProviderPackageCandidate]:
        try:
            output = self.search_client.search(
                activity_name=activity_name,
                region=region,
                query=query,
                limit=limit,
            )
        except Exception:
            return []
        candidates: list[ProviderPackageCandidate] = []
        for index, candidate in enumerate(output.candidates, start=1):
            checkout_url = candidate.checkout_url
            source_url = candidate.source_url
            if _is_disallowed_checkout_url(checkout_url) or _is_disallowed_checkout_url(source_url):
                continue
            if not _agent_validation_claim_is_sufficient(candidate):
                continue
            checkout_validation = self.link_validator.validate(
                url=checkout_url, activity_name=activity_name, region=region
            )
            source_validation = checkout_validation
            if not checkout_validation.valid and source_url != checkout_url:
                source_validation = self.link_validator.validate(
                    url=source_url, activity_name=activity_name, region=region
                )
            validation = checkout_validation if checkout_validation.valid else source_validation
            if not validation.valid:
                continue
            checkout_url = validation.final_url or checkout_url
            source_url = source_validation.final_url or source_url
            try:
                candidates.append(
                    ProviderPackageCandidate(
                        package_id=f"agentic-{index}",
                        name=candidate.name,
                        description=candidate.description,
                        provider=_provider_for_name(candidate.provider_name),
                        provider_name=candidate.provider_name,
                        checkout_url=checkout_url,
                        source_url=source_url,
                        confidence=_normalize_confidence(candidate.confidence),
                        price_summary=candidate.price_summary,
                        cancellation_summary=candidate.cancellation_summary,
                        validation_summary=validation.summary,
                        source_type=validation.source_type or candidate.source_type,
                        relevance_rationale=validation.relevance_rationale or candidate.relevance_rationale,
                        caveats=[
                            *candidate.caveats,
                            "External checkout; final purchase happens outside Wanderlust.",
                            "Confirm provider identity, final price, and availability before payment.",
                        ],
                        stripe_backed=candidate.stripe_backed
                        or "buy.stripe.com" in candidate.checkout_url,
                    )
                )
            except ValueError:
                continue
        return candidates[:limit]


class GeminiProviderPackageSearchClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.model = settings.gemini_model
        self.google_api_key = settings.google_api_key
        self._client: genai.Client | None = None

    def _get_client(self) -> genai.Client:
        if self._client is not None:
            return self._client
        if not self.google_api_key or self.google_api_key.startswith("test-"):
            raise RuntimeError("GOOGLE_API_KEY is required for package search grounding.")
        self._client = genai.Client(api_key=self.google_api_key)
        return self._client

    def search(
        self,
        *,
        activity_name: str,
        region: str,
        query: str,
        limit: int,
    ) -> AgenticProviderPackageOutput:
        prompt = json.dumps(
            {
                "task": (
                    "Use Google Search grounding to find current official or authorized "
                    "tickets, tours, packages, provider checkout/search pages, or public "
                    "Stripe payment links for exactly this selected activity. Return strict JSON only."
                ),
                "adk_workflow": "package_search_sequence",
                "activity_name": activity_name,
                "region": region,
                "query": query,
                "response_schema": {
                    "candidates": [
                        {
                            "name": "package or provider offer title",
                            "description": "what the package includes",
                            "provider_name": "official venue or provider name",
                            "checkout_url": "https://...",
                            "source_url": "https://...",
                            "confidence": "high|medium|low",
                            "price_summary": "price if source states one",
                            "cancellation_summary": "refund/cancellation caveat if source states one",
                            "validation_summary": "how you checked the page was reachable, relevant, and not an error page",
                            "source_type": "official|authorized_reseller|known_provider|public_stripe_link|provider_discovery",
                            "relevance_rationale": "why this page is specifically about the selected activity",
                            "caveats": ["uncertainty or availability caveats"],
                            "stripe_backed": False,
                        }
                    ],
                    "evidence_note": "short source summary",
                },
                "guardrails": [
                    "Do not return unrelated city-wide products.",
                    "Prefer official venue, authorized reseller, known travel provider, or public Stripe links.",
                    "Visually or textually validate that each checkout_url/source_url resolves to a valid page, is not a 404/not-found/error page, and is relevant to the selected activity.",
                    "Do not use Google search result pages, generic search pages, dead links, or unrelated provider homepages as checkout_url.",
                    "Do not invent price, availability, refund terms, validation evidence, or checkout URLs.",
                    "Only return HTTPS URLs.",
                    "If you cannot validate a candidate page, omit it instead of guessing.",
                    f"Return at most {limit} candidates.",
                ],
            },
            ensure_ascii=True,
        )
        client = self._get_client()
        interactions = getattr(client, "interactions", None)
        if interactions is None:
            raise RuntimeError("Gemini Interactions API is unavailable.")
        interaction = interactions.create(
            model=self.model,
            input=prompt,
            tools=[{"type": "google_search"}],
        )
        text = getattr(interaction, "output_text", None)
        if not text:
            raise RuntimeError("Google Search grounding returned no package output.")
        return AgenticProviderPackageOutput.model_validate(_parse_json_response(text))


def _page_text_snippet(html: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()[:8000]


def _important_terms(value: str) -> list[str]:
    ignored = {"the", "and", "with", "for", "from", "tour", "ticket", "tickets", "package", "packages"}
    terms = []
    for term in re.findall(r"[a-z0-9]+", value.lower()):
        if len(term) >= 3 and term not in ignored and term not in terms:
            terms.append(term)
    return terms[:8]


def _provider_for_name(provider_name: str) -> CheckoutProvider:
    normalized = provider_name.lower()
    if "getyourguide" in normalized:
        return CheckoutProvider.GETYOURGUIDE
    if "viator" in normalized:
        return CheckoutProvider.VIATOR
    if "klook" in normalized:
        return CheckoutProvider.KLOOK
    if "pelago" in normalized:
        return CheckoutProvider.PELAGO
    if "tiqets" in normalized:
        return CheckoutProvider.TIQETS
    if "headout" in normalized:
        return CheckoutProvider.HEADOUT
    if "stripe" in normalized:
        return CheckoutProvider.STRIPE_LINK
    return CheckoutProvider.OFFICIAL


def _normalize_confidence(value: str) -> str:
    normalized = value.lower().strip()
    return normalized if normalized in {"high", "medium", "low"} else "medium"


def _parse_json_response(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    return json.loads(stripped)


def _is_disallowed_checkout_url(value: str) -> bool:
    parsed = urlparse(value)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    query = parsed.query.lower()
    if parsed.scheme != "https" or not host:
        return True
    if "google." in host and ("/search" in path or "q=" in query):
        return True
    if any(token in path for token in ("404", "not-found", "notfound", "error")):
        return True
    return False


def _agent_validation_claim_is_sufficient(candidate: AgenticProviderPackageCandidate) -> bool:
    validation = (candidate.validation_summary or "").strip().lower()
    rationale = (candidate.relevance_rationale or "").strip().lower()
    source_type = (candidate.source_type or "").strip().lower()
    if not validation or not rationale:
        return False
    if "not found" in validation or "404" in validation or "unreachable" in validation:
        return False
    return source_type in {
        "official",
        "authorized_reseller",
        "known_provider",
        "public_stripe_link",
        "provider_discovery",
    }


class StripeCommerceService(ProviderCommerceService):
    """Compatibility alias for older imports while commerce is provider-based."""
