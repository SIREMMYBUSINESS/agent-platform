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
    "DATABASE_URL_FILE",
    "REDIS_URL_FILE",
    "PROVIDER_SECRETS_DIR",
    "WORKER_BOOTSTRAP_MODULE",
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


def test_worker_bootstrap_must_be_an_importable_module_path(monkeypatch):
    clear_settings_environment(monkeypatch)
    settings = Settings(_env_file=None, worker_bootstrap_module="deployment.worker_handlers")
    assert settings.worker_bootstrap_module == "deployment.worker_handlers"
    with pytest.raises(ValidationError, match="Python module path"):
        Settings(_env_file=None, worker_bootstrap_module="../outside")


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
    database_url_file = tmp_path / "database_url"
    database_url_file.write_text(
        "postgresql://user:password@db.example/production_db?sslmode=verify-full\n",
        encoding="utf-8",
    )
    database_url_file.chmod(0o400)
    redis_url_file = tmp_path / "redis_url"
    redis_url_file.write_text("rediss://cache.example/1\n", encoding="utf-8")
    redis_url_file.chmod(0o400)
    (tmp_path / ".env").write_text(
        "ENVIRONMENT=production\nDATABASE_URL=postgresql://localhost/base_db\n"
        f"DATABASE_URL_FILE={database_url_file}\nREDIS_URL_FILE={redis_url_file}\n",
        encoding="utf-8",
    )
    (tmp_path / ".env.production").write_text(
        f"DATABASE_URL_FILE={database_url_file}\n"
        f"REDIS_URL_FILE={redis_url_file}\n",
        encoding="utf-8",
    )

    settings = load_settings()

    assert settings.environment == "production"
    assert settings.database_url == "postgresql://user:password@db.example/production_db?sslmode=verify-full"
    assert settings.redis_url == "rediss://cache.example/1"


def test_database_url_can_be_read_from_a_mounted_secret_file(monkeypatch, tmp_path):
    from app.config import load_settings

    clear_settings_environment(monkeypatch)
    secret_file = tmp_path / "database_url"
    secret_file.write_text("postgresql+psycopg://user:password@db.example/platform\n", encoding="utf-8")
    secret_file.chmod(0o400)
    monkeypatch.setenv("DATABASE_URL_FILE", str(secret_file))
    monkeypatch.chdir(tmp_path)

    settings = load_settings()

    assert settings.database_url == "postgresql+psycopg://user:password@db.example/platform"


def test_production_rejects_unverified_database_tls_and_inline_credentials(monkeypatch, tmp_path):
    from app.config import load_settings

    clear_settings_environment(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@db.example/platform")
    monkeypatch.setenv("REDIS_URL", "rediss://cache.example/0")
    with pytest.raises(RuntimeError, match="TLS certificate verification"):
        load_settings()

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@db.example/platform?sslmode=verify-full")
    with pytest.raises(RuntimeError, match="DATABASE_URL_FILE"):
        load_settings()


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
