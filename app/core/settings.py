from functools import lru_cache

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

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""
    public_backend_base_url: str = ""
    gemini_live_model: str = "gemini-3.1-flash-live-preview"
    booking_call_max_seconds: int = 300
    call_log_backend: str = "disabled"
    call_log_collection: str = "wanderlust_booking_call_logs"
    wanderlust_storage_backend: str = "sqlite"
    firestore_collection_prefix: str = "wanderlust"

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
        missing: list[str] = []
        if self.use_vertex_ai and not self.vertex_ai_location:
            missing.append("VERTEX_AI_LOCATION")
        return missing


@lru_cache
def get_settings() -> Settings:
    return Settings()
