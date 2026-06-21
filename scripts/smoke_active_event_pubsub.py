from __future__ import annotations

import os
import sys

from app.domain.models import GeoPoint, LocationEvent
from app.services.active_events import PubSubLocationEventPublisher


def main() -> int:
    if os.getenv("RUN_REAL_INTEGRATION") != "1":
        print("Skipped. Set RUN_REAL_INTEGRATION=1 to publish a real Pub/Sub location event.")
        return 0

    event = LocationEvent(
        id="smoke-location-event",
        itinerary_id=os.getenv("SMOKE_ITINERARY_ID", "smoke-itinerary"),
        user_id=os.getenv("SMOKE_USER_ID", "smoke-user"),
        location=GeoPoint(latitude=1.3521, longitude=103.8198, accuracy_meters=25),
        occurred_at="2026-06-21T10:00:00+08:00",
        context_signal="smoke_test",
    )
    message_id = PubSubLocationEventPublisher().publish(event)
    print({"published_event_id": message_id, "itinerary_id": event.itinerary_id})
    return 0


if __name__ == "__main__":
    sys.exit(main())
