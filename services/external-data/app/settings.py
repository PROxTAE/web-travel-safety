from __future__ import annotations

from pydantic import Field, SecretStr
from sta_common.settings import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "external-data"
    service_port: int = 8002

    # storage
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "smart_travel"
    postgres_provider_password: SecretStr = SecretStr("")
    postgres_user_override: str | None = Field(default=None, alias="POSTGRES_PROVIDER_USER")
    redis_url: str = "redis://redis:6379/0"

    # providers (base URLs from config only — never from user input)
    open_meteo_base_url: str = "https://api.open-meteo.com"
    open_meteo_geocoding_url: str = "https://geocoding-api.open-meteo.com"
    ors_base_url: str = "https://api.openrouteservice.org"
    ors_api_key: SecretStr = SecretStr("")
    amadeus_base_url: str = "https://api.amadeus.com"
    amadeus_client_id: SecretStr = SecretStr("")
    amadeus_client_secret: SecretStr = SecretStr("")
    usgs_feed_url: str = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson"
    usgs_query_url: str = "https://earthquake.usgs.gov/fdsnws/event/1/query"
    gdacs_base_url: str = "https://www.gdacs.org/gdacsapi"
    eonet_base_url: str = "https://eonet.gsfc.nasa.gov/api/v3"
    gtfs_provider_config: str = "config/providers.yaml"
    gtfs_cache_dir: str = "data/gtfs-cache"

    # freshness TTLs (seconds) — 00_SHARED_PROJECT_CONTEXT §10
    ttl_geocode: int = 24 * 3600
    ttl_current_weather: int = 15 * 60
    ttl_hourly_forecast: int = 60 * 60
    ttl_disaster: int = 10 * 60
    ttl_severe_alert: int = 5 * 60
    ttl_route: int = 6 * 3600
    ttl_gtfs_rt: int = 90
    ttl_places: int = 6 * 3600
    ttl_negative: int = 30

    # reliability
    circuit_failure_threshold: int = 5
    circuit_open_seconds: int = 60
    provider_max_concurrency: int = 8
    context_deadline_seconds: float = 20.0
    disaster_lookback_days: int = 7
    disaster_bbox_buffer_deg: float = 1.0
    earthquake_min_magnitude: float = 2.5
    route_max_weather_samples: int = 12
    delay_probe_minutes: int = 360  # second weather probe at ETA + 6 h for DELAY reasoning
    places_max_radius_m: int = 20000
    places_max_results: int = 20

    @property
    def db_user(self) -> str:
        return self.postgres_user_override or "sta_provider"

    @property
    def ors_enabled(self) -> bool:
        return bool(self.ors_api_key.get_secret_value())

    @property
    def amadeus_enabled(self) -> bool:
        return bool(self.amadeus_client_id.get_secret_value() and self.amadeus_client_secret.get_secret_value())


def get_settings() -> Settings:
    return Settings()
