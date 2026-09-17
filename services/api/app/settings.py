from __future__ import annotations

from pydantic import Field, SecretStr, field_validator
from sta_common.settings import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "api"
    service_port: int = 8000

    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "smart_travel"
    postgres_api_password: SecretStr = SecretStr("")
    postgres_user_override: str | None = Field(default=None, alias="POSTGRES_API_USER")
    redis_url: str = "redis://redis:6379/0"

    # OIDC (Keycloak). Browser tokens carry the public issuer; the API may also accept the internal issuer
    # when the realm is reached through the docker network (both point at the same realm keys).
    oidc_issuer: str = "http://localhost:8080/realms/smart-travel"
    oidc_internal_issuer: str | None = "http://keycloak:8080/realms/smart-travel"
    oidc_audience: str = "smart-travel-api"
    oidc_jwks_url: str | None = None  # default: <internal issuer or issuer>/protocol/openid-connect/certs
    oidc_jwks_ttl_seconds: int = 600
    oidc_required_role: str = "traveler"
    oidc_clock_skew_seconds: int = 30

    agent_service_url: str = "http://agent:8001"
    external_data_service_url: str = "http://external-data:8002"
    recommendation_service_url: str = "http://recommendation:8006"

    cors_allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    trusted_proxy_count: int = Field(default=0, ge=0, le=3, description="X-Forwarded-For hops to trust")

    # Envelope encryption for identity.emergency_profiles (32-byte base64 key, kept out of the DB)
    emergency_profile_encryption_key: SecretStr = SecretStr("")
    emergency_profile_key_version: int = 1
    # Salt for pseudonymous user scopes handed to downstream services (never the subject id itself)
    user_scope_salt: SecretStr = SecretStr("")

    # Rate limits (fixed 60 s windows per subject and endpoint class)
    rate_limit_default_per_minute: int = 120
    rate_limit_assessment_per_minute: int = 10
    rate_limit_search_per_minute: int = 30
    rate_limit_anonymous_per_minute: int = 30
    max_sse_connections_per_user: int = 3

    # Downstream budgets (seconds)
    agent_submit_timeout_seconds: float = 5.0
    agent_poll_timeout_seconds: float = 4.0
    external_data_timeout_seconds: float = 8.0
    recommendation_timeout_seconds: float = 6.0

    idempotency_ttl_seconds: int = 24 * 3600
    sse_heartbeat_seconds: float = 15.0
    sse_max_duration_seconds: float = 120.0
    sse_poll_interval_seconds: float = 0.5
    max_trip_horizon_days: int = 365
    max_past_departure_minutes: int = 30
    safety_bbox_max_degrees: float = 10.0
    retention_days: int = 90
    alerts_consumer_enabled: bool = True
    alerts_consumer_group: str = "api"
    alerts_stream: str = "alert.reassessment.requested"

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @field_validator("emergency_profile_encryption_key")
    @classmethod
    def _key_required_outside_test(cls, v: SecretStr, info) -> SecretStr:  # type: ignore[no-untyped-def]
        env = info.data.get("app_env", "development")
        if env in ("staging", "production") and not v.get_secret_value():
            raise ValueError("EMERGENCY_PROFILE_ENCRYPTION_KEY is required outside development/test")
        return v

    @property
    def db_user(self) -> str:
        return self.postgres_user_override or "sta_api"

    @property
    def jwks_url(self) -> str:
        base = self.oidc_jwks_url or f"{self.oidc_internal_issuer or self.oidc_issuer}/protocol/openid-connect/certs"
        return base

    @property
    def accepted_issuers(self) -> list[str]:
        return [i for i in (self.oidc_issuer, self.oidc_internal_issuer) if i]


def get_settings() -> Settings:
    return Settings()
