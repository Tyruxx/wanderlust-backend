from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel

from app.core.settings import get_settings
from app.domain.models import SourceConfidence, SourceEvidence, SourceType


class MapsIntegrationError(RuntimeError):
    pass


class CandidatePlace(BaseModel):
    place_id: str
    name: str
    formatted_address: str = ""
    primary_type: str = "point_of_interest"
    rating: float | None = None
    website_uri: str | None = None
    google_maps_uri: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    def to_evidence(self) -> SourceEvidence:
        return SourceEvidence(
            source_type=SourceType.GOOGLE_PLACES,
            title=self.name,
            url=self.google_maps_uri,
            confidence=SourceConfidence.HIGH,
            freshness_note="Google Places API candidate returned during itinerary generation.",
            claims=[self.formatted_address] if self.formatted_address else [],
        )


@dataclass(frozen=True)
class Coordinates:
    latitude: float
    longitude: float


class GoogleMapsClient:
    PLACES_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
    ROUTES_COMPUTE_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
    GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
    WEATHER_CURRENT_URL = "https://weather.googleapis.com/v1/currentConditions:lookup"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.maps_backend_api_key
        self.http_client = http_client or httpx.Client(timeout=settings.request_timeout_seconds)

    def text_search(
        self,
        query: str,
        *,
        region: str,
        max_result_count: int = 6,
    ) -> list[CandidatePlace]:
        self._require_api_key()
        response = self.http_client.post(
            self.PLACES_TEXT_SEARCH_URL,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": ",".join(
                    [
                        "places.id",
                        "places.displayName",
                        "places.formattedAddress",
                        "places.primaryType",
                        "places.rating",
                        "places.websiteUri",
                        "places.googleMapsUri",
                        "places.location",
                    ]
                ),
            },
            json={
                "textQuery": f"{query} in {region}",
                "maxResultCount": max_result_count,
            },
        )
        response.raise_for_status()
        return [_candidate_from_place(place) for place in response.json().get("places", [])]

    def geocode(self, address: str) -> Coordinates | None:
        self._require_api_key()
        response = self.http_client.get(
            self.GEOCODE_URL,
            params={"address": address, "key": self.api_key},
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        if not results:
            return None
        location = results[0].get("geometry", {}).get("location", {})
        if "lat" not in location or "lng" not in location:
            return None
        return Coordinates(latitude=float(location["lat"]), longitude=float(location["lng"]))

    def current_weather(self, coordinates: Coordinates) -> dict[str, Any]:
        self._require_api_key()
        response = self.http_client.get(
            self.WEATHER_CURRENT_URL,
            params={
                "key": self.api_key,
                "location.latitude": coordinates.latitude,
                "location.longitude": coordinates.longitude,
            },
        )
        response.raise_for_status()
        return response.json()

    def compute_route(
        self,
        *,
        origin: Coordinates,
        destination: Coordinates,
        travel_mode: str = "WALK",
    ) -> dict[str, Any]:
        self._require_api_key()
        response = self.http_client.post(
            self.ROUTES_COMPUTE_URL,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": "routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline",
            },
            json={
                "origin": {
                    "location": {
                        "latLng": {
                            "latitude": origin.latitude,
                            "longitude": origin.longitude,
                        }
                    }
                },
                "destination": {
                    "location": {
                        "latLng": {
                            "latitude": destination.latitude,
                            "longitude": destination.longitude,
                        }
                    }
                },
                "travelMode": travel_mode,
            },
        )
        response.raise_for_status()
        return response.json()

    def _require_api_key(self) -> None:
        if not self.api_key:
            raise MapsIntegrationError("GOOGLE_MAPS_BACKEND_API_KEY is required for Maps calls.")


def _candidate_from_place(place: dict[str, Any]) -> CandidatePlace:
    display_name = place.get("displayName", {})
    location = place.get("location", {})
    return CandidatePlace(
        place_id=str(place.get("id", "")),
        name=str(display_name.get("text") or place.get("name") or "Unnamed place"),
        formatted_address=str(place.get("formattedAddress") or ""),
        primary_type=str(place.get("primaryType") or "point_of_interest"),
        rating=place.get("rating"),
        website_uri=place.get("websiteUri"),
        google_maps_uri=place.get("googleMapsUri"),
        latitude=location.get("latitude"),
        longitude=location.get("longitude"),
    )
