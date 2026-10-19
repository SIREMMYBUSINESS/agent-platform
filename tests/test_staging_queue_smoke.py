import pytest

from app.staging_queue_smoke import _require_neon_database, _require_staging_opt_in


def test_staging_queue_smoke_requires_exact_staging_environment():
    with pytest.raises(RuntimeError, match="ENVIRONMENT must be staging"):
        _require_staging_opt_in("production", "true")


def test_staging_queue_smoke_requires_explicit_opt_in():
    with pytest.raises(RuntimeError, match="STAGING_QUEUE_SMOKE_ENABLED=true"):
        _require_staging_opt_in("staging", "")


@pytest.mark.parametrize("opt_in", ["1", "true", "YES"])
def test_staging_queue_smoke_accepts_staging_opt_in(opt_in):
    _require_staging_opt_in("staging", opt_in)


def test_staging_queue_smoke_rejects_non_neon_database_hosts():
    with pytest.raises(RuntimeError, match="database host must be a Neon host"):
        _require_neon_database("postgresql://user:secret@production.example/db")


def test_staging_queue_smoke_accepts_neon_database_hosts():
    _require_neon_database("postgresql://user:secret@ep-staging.eu-central-1.aws.neon.tech/db")
