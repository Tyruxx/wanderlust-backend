from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"
    app_name: str = "Wanderlust Trip Backend"
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    backend_base_url: str = "http://127.0.0.1:8000"
    frontend_base_url: str = "http://127.0.0.1:5713"
    cors_allowed_origins: str = "http://127.0.0.1:5713,http://localhost:5713"

    google_cloud_project: str = ""
    google_cloud_region: str = "asia-southeast1"
    firebase_project_id: str = ""
    firebase_web_api_key: str = ""
    firebase_ios_bundle_id: str = "com.example.wanderlustTrip"
    google_ios_client_id: str = ""
    google_ios_reversed_client_id: str = ""
    google_server_client_id: str = ""
    google_android_client_id: str = ""
    google_web_client_id: str = ""

    use_vertex_ai: bool = True
    vertex_ai_location: str = "asia-southeast1"
    gemini_model: str = "gemini-2.5-flash"
    google_api_key: str = ""

    google_maps_backend_api_key: str = ""
    google_maps_ios_api_key: str = ""
    google_maps_api_key: str = ""
    google_places_api_key: str = ""
    google_routes_api_key: str = ""
    google_geocoding_api_key: str = ""
    google_weather_api_key: str = ""

    firestore_database_id: str = Field(default="(default)")
    pubsub_location_events_topic: str = "location-events"
    pubsub_agent_runs_topic: str = "agent-runs"
    pubsub_notifications_topic: str = "notifications"

    jwt_audience: str = ""
    log_level: str = "INFO"
    request_timeout_seconds: int = 30
    agent_timeout_seconds: int = 60

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @property
    def maps_backend_api_key(self) -> str:
        return (
            self.google_maps_backend_api_key
            or self.google_maps_api_key
            or self.google_places_api_key
            or self.google_routes_api_key
            or self.google_geocoding_api_key
            or self.google_weather_api_key
        )

    @property
    def missing_required_values(self) -> list[str]:
        required = {
            "GOOGLE_CLOUD_PROJECT": self.google_cloud_project,
            "FIREBASE_PROJECT_ID": self.firebase_project_id,
        }
        if self.use_vertex_ai:
            required["VERTEX_AI_LOCATION"] = self.vertex_ai_location
        elif not self.google_api_key:
            required["GOOGLE_API_KEY"] = self.google_api_key
        return [key for key, value in required.items() if not value]


@lru_cache
def get_settings() -> Settings:
    return Settings()
