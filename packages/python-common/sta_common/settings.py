"""Base settings shared by every service. Services subclass and add their own fields."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    app_env: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"
    service_name: str = Field(default="service")
    service_port: int = Field(default=8000, ge=1, le=65535)
    contract_version: str = "1.0.0"
    # Shared bearer token for /internal/v1 calls between services on the docker network.
    service_auth_token: SecretStr = Field(default=SecretStr(""))

    otel_exporter_otlp_endpoint: str | None = None
    otel_traces_enabled: bool = True

    # Outbound HTTP defaults (seconds). Each client may override per dependency.
    http_connect_timeout: float = 3.0
    http_read_timeout: float = 10.0
    http_total_timeout: float = 12.0
    http_max_retries: int = 2

    @field_validator("service_auth_token")
    @classmethod
    def _token_required_outside_test(cls, v: SecretStr, info) -> SecretStr:  # type: ignore[no-untyped-def]
        env = info.data.get("app_env", "development")
        if env in ("staging", "production") and not v.get_secret_value():
            raise ValueError("SERVICE_AUTH_TOKEN is required outside development/test")
        return v

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"
