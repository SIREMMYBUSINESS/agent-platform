from pydantic import ValidationError
import pytest

from app.config import Settings


SETTING_ENV_VARS = (
    "APP_NAME",
    "ENVIRONMENT",
    "LOG_LEVEL",
    "DEFAULT_REGION",
    "INBOUND_TENANT_ID",
    "DATABASE_URL",
    "REDIS_URL",
)


def clear_settings_environment(monkeypatch):
    for name in SETTING_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_settings_have_safe_local_defaults(monkeypatch):
    clear_settings_environment(monkeypatch)

    settings = Settings(_env_file=None)

    assert settings.app_name == "Receptionist + Outreach Agent Platform"
    assert settings.environment == "development"
    assert settings.log_level == "INFO"
    assert settings.default_region == "US"
    assert settings.inbound_tenant_id == "tenant-1"
    assert settings.database_url == "postgresql://localhost:5432/agent_platform"
    assert settings.redis_url == "redis://localhost:6379/0"


def test_api_uses_validated_app_name_setting():
    from app.main import app

    assert app.title == "Receptionist + Outreach Agent Platform"


def test_environment_variables_override_dotenv_values(monkeypatch, tmp_path):
    clear_settings_environment(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text("APP_NAME=From dotenv\nLOG_LEVEL=WARNING\n", encoding="utf-8")
    monkeypatch.setenv("APP_NAME", "From environment")

    settings = Settings(_env_file=env_file)

    assert settings.app_name == "From environment"
    assert settings.log_level == "WARNING"


def test_postgresql_and_redis_environment_settings_load(monkeypatch):
    clear_settings_environment(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://db.example/platform")
    monkeypatch.setenv("REDIS_URL", "rediss://cache.example:6380/2")
    monkeypatch.setenv("INBOUND_TENANT_ID", "tenant-production")

    settings = Settings(_env_file=None)

    assert settings.database_url.startswith("postgresql+psycopg://")
    assert settings.redis_url == "rediss://cache.example:6380/2"
    assert settings.inbound_tenant_id == "tenant-production"


def test_environment_specific_dotenv_overrides_base_dotenv(monkeypatch, tmp_path):
    from app.config import load_settings

    clear_settings_environment(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "ENVIRONMENT=production\nDATABASE_URL=postgresql://localhost/base_db\n",
        encoding="utf-8",
    )
    (tmp_path / ".env.production").write_text(
        "DATABASE_URL=postgresql://db.example/production_db\n"
        "REDIS_URL=rediss://cache.example/1\n",
        encoding="utf-8",
    )

    settings = load_settings()

    assert settings.environment == "production"
    assert settings.database_url == "postgresql://db.example/production_db"
    assert settings.redis_url == "rediss://cache.example/1"


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("LOG_LEVEL", "verbose", "must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL"),
        ("DATABASE_URL", "not-a-url", "must be a PostgreSQL URL with a host and database name"),
        ("REDIS_URL", "http://localhost:6379", "must be a redis:// or rediss:// URL with a host"),
        ("INBOUND_TENANT_ID", " ", "must not be blank"),
    ],
)
def test_invalid_environment_settings_raise_clear_errors(monkeypatch, name, value, message):
    clear_settings_environment(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)

    assert message in str(error.value)
