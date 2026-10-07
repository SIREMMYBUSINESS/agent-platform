import os
import re
import stat
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

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
    # Legacy dispatcher configuration; protected HTTP routes use the authenticated principal.
    inbound_tenant_id: str = Field(default="tenant-1", min_length=1)
    # Optional trusted business scope for the lead-management API.
    lead_business_id: str | None = None

    # Local service addresses only; credentials belong in the environment or .env.
    database_url: str = "postgresql://localhost:5432/agent_platform"
    database_url_file: Path | None = None
    redis_url_file: Path | None = None
    provider_secrets_dir: Path | None = None
    worker_bootstrap_module: str | None = None
    workflow_dry_run: bool = False
    optimization_min_sample_size: int = Field(default=30, ge=2, le=100000)
    optimization_confidence_threshold: float = Field(default=0.75, ge=0.5, le=1.0)
    optimization_dry_run: bool = False
    public_app_url: str = "http://localhost:3000"
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

    @field_validator("worker_bootstrap_module")
    @classmethod
    def validate_worker_bootstrap_module(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        if not re.fullmatch(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*", value):
            raise ValueError("must be a Python module path")
        return value

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

    @field_validator("public_app_url")
    @classmethod
    def validate_public_app_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.query or parsed.fragment:
            raise ValueError("must be an HTTP(S) application URL without query or fragment")
        return value.rstrip("/")

def load_settings() -> Settings:
    """Load base dotenv, then environment-specific dotenv, then OS values."""
    base_settings = Settings()
    environment = os.environ.get("ENVIRONMENT", base_settings.environment).strip()
    environment_file = Path(f".env.{environment}")
    if environment_file.is_file():
        base_settings = Settings(_env_file=(Path(".env"), environment_file))
    secret_values = {}
    for env_name, field_name in (("DATABASE_URL_FILE", "database_url"),
                                 ("REDIS_URL_FILE", "redis_url")):
        secret_file = os.environ.get(env_name) or getattr(base_settings, env_name.lower())
        if not secret_file:
            continue
        try:
            path = Path(secret_file)
            if path.is_symlink():
                raise OSError
            metadata = path.stat()
            if not stat.S_ISREG(metadata.st_mode):
                raise OSError
            if os.name != "nt":
                mode = stat.S_IMODE(metadata.st_mode)
                group_ids = set(os.getgroups()) | {os.getgid()}
                if (mode & 0o007 or mode & 0o222
                        or not mode & 0o400 and not (mode & 0o040 and metadata.st_gid in group_ids)):
                    raise OSError
            secret_value = path.read_text(encoding="utf-8").strip()
        except OSError:
            raise RuntimeError(f"{env_name} could not be read") from None
        if not secret_value:
            raise RuntimeError(f"{env_name} is empty")
        secret_values[field_name] = secret_value
    if secret_values:
        base_settings = Settings(**(base_settings.model_dump() | secret_values))
    if base_settings.environment.lower() == "production":
        database = urlsplit(base_settings.database_url)
        sslmode = parse_qs(database.query).get("sslmode", [""])[-1]
        if sslmode not in {"verify-full", "verify-ca"}:
            raise RuntimeError("production DATABASE_URL must require TLS certificate verification")
        if urlsplit(base_settings.redis_url).scheme != "rediss":
            raise RuntimeError("production REDIS_URL must use rediss://")
        if base_settings.database_url_file is None:
            raise RuntimeError("production DATABASE_URL must be supplied through DATABASE_URL_FILE")
        if base_settings.redis_url_file is None:
            raise RuntimeError("production REDIS_URL must be supplied through REDIS_URL_FILE")
    return base_settings


settings = load_settings()
