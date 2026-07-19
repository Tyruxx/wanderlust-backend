from __future__ import annotations

import json
import unittest

import httpx

from app.services.maps import Coordinates, GoogleMapsClient, MapsIntegrationError


class GoogleMapsClientTests(unittest.TestCase):
    def test_text_search_uses_places_search_text_endpoint_and_field_mask(self) -> None:
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["api_key"] = request.headers.get("X-Goog-Api-Key")
            seen["field_mask"] = request.headers.get("X-Goog-FieldMask")
            seen["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "places": [
                        {
                            "id": "abc123",
                            "displayName": {"text": "Maxwell Food Centre"},
                            "formattedAddress": "Singapore",
                            "primaryType": "restaurant",
                            "rating": 4.5,
                            "googleMapsUri": "https://maps.google.com/?cid=abc123",
                            "location": {"latitude": 1.28, "longitude": 103.84},
                        }
                    ]
                },
            )

        client = GoogleMapsClient(
            api_key="maps-key",
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        results = client.text_search("food", region="Singapore")

        self.assertEqual(results[0].name, "Maxwell Food Centre")
        self.assertEqual(seen["url"], GoogleMapsClient.PLACES_TEXT_SEARCH_URL)
        self.assertEqual(seen["api_key"], "maps-key")
        self.assertIn("places.displayName", str(seen["field_mask"]))
        self.assertEqual(seen["body"], {"textQuery": "food in Singapore", "maxResultCount": 6})

    def test_geocode_and_weather_use_expected_request_shapes(self) -> None:
        urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            urls.append(str(request.url))
            if "geocode" in str(request.url):
                return httpx.Response(
                    200,
                    json={"results": [{"geometry": {"location": {"lat": 1.29, "lng": 103.85}}}]},
                )
            return httpx.Response(
                200,
                json={"weatherCondition": {"description": {"text": "Warm"}}},
            )

        client = GoogleMapsClient(
            api_key="maps-key",
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        coords = client.geocode("Singapore")
        self.assertEqual(coords, Coordinates(latitude=1.29, longitude=103.85))
        weather = client.current_weather(coords)

        self.assertEqual(weather["weatherCondition"]["description"]["text"], "Warm")
        self.assertTrue(any(GoogleMapsClient.GEOCODE_URL in url for url in urls))
        self.assertTrue(any(GoogleMapsClient.WEATHER_CURRENT_URL in url for url in urls))

    def test_expired_key_error_is_sanitized(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                request=request,
                json={
                    "error": {
                        "status": "INVALID_ARGUMENT",
                        "message": "API key expired. Secret value must not reach clients.",
                        "details": [{"reason": "API_KEY_INVALID"}],
                    }
                },
            )

        client = GoogleMapsClient(
            api_key="expired-key",
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        with self.assertRaisesRegex(
            MapsIntegrationError,
            "Google Maps credentials are temporarily unavailable",
        ) as context:
            client.text_search("food", region="Singapore")

        self.assertNotIn("Secret value", str(context.exception))


if __name__ == "__main__":
    unittest.main()
