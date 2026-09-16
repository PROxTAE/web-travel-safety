from __future__ import annotations

from pydantic import Field, SecretStr
from sta_common.settings import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "data-integration"
    service_port: int = 8003

    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "smart_travel"
    postgres_integration_password: SecretStr = SecretStr("")
    postgres_user_override: str | None = Field(default=None, alias="POSTGRES_INTEGRATION_USER")
    redis_url: str = "redis://redis:6379/0"

    # versions
    schema_version: str = "1.0.0"
    feature_schema_version: str = "1.0.0"
    transform_version: str = "1.0.0"
    quality_weights_version: str = "1.0.0"
    feature_schema_path: str = "config/feature_schema.yaml"

    # corridor geometry (metres); mode-specific buffers are in config/corridor.yaml semantics below
    corridor_buffer_m_ground: float = 15_000
    corridor_buffer_m_flight: float = 60_000
    corridor_densify_m: float = 5_000
    weather_match_tolerance_seconds: int = 3 * 3600
    weather_match_max_distance_m: float = 40_000
    # coverage denominator: expected samples along the route (same rule as คน 4 sampler cap)
    weather_expected_spacing_m: float = 50_000
    weather_expected_samples_max: int = 12

    # freshness thresholds (seconds) used by the quality gate — 00_SHARED_PROJECT_CONTEXT §10
    fresh_severe_alert_s: int = 5 * 60
    fresh_disaster_s: int = 10 * 60
    fresh_weather_s: int = 60 * 60
    fresh_transport_s: int = 90
    fresh_route_s: int = 6 * 3600

    # quality gate thresholds (versioned with quality_weights_version)
    gate_block_score: float = 0.25
    gate_degraded_score: float = 0.6
    min_weather_coverage_for_pass: float = 0.5

    @property
    def db_user(self) -> str:
        return self.postgres_user_override or "sta_integration"


def get_settings() -> Settings:
    return Settings()
