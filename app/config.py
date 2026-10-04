import os
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_name: str = Field(default="Receptionist + Outreach Agent Platform", min_length=1)
    environment: str = Field(default="development", min_length=1)
    log_level: str = "INFO"
    default_region: str = Field(default="US", min_length=1)
    # Trusted single-tenant routing context for the current inbound endpoint.
    inbound_tenant_id: str = Field(default="tenant-1", min_length=1)
    # Optional trusted business scope for the lead-management API.
    lead_business_id: str | None = None

    # Local service addresses only; credentials belong in the environment or .env.
    database_url: str = "postgresql://localhost:5432/agent_platform"
    redis_url: str = "redis://localhost:6379/0"

    @field_validator("app_name", "environment", "default_region", "inbound_tenant_id")
    @classmethod
    def validate_nonblank_values(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("lead_business_id")
    @classmethod
    def validate_optional_lead_business_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        value = value.strip().upper()
        allowed_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if value not in allowed_levels:
            raise ValueError("must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL")
        return value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        is_postgresql = parsed.scheme == "postgresql" or parsed.scheme.startswith("postgresql+")
        if not is_postgresql or not parsed.hostname or not parsed.path.strip("/"):
            raise ValueError("must be a PostgreSQL URL with a host and database name")
        return value

    @field_validator("redis_url")
    @classmethod
    def validate_redis_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
            raise ValueError("must be a redis:// or rediss:// URL with a host")
        return value


def load_settings() -> Settings:
    """Load base dotenv, then environment-specific dotenv, then OS values."""
    base_settings = Settings()
    environment = os.environ.get("ENVIRONMENT", base_settings.environment).strip()
    environment_file = Path(f".env.{environment}")
    if environment_file.is_file():
        return Settings(_env_file=(Path(".env"), environment_file))
    return base_settings


settings = load_settings()
