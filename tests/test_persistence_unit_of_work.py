import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlmodel import Session

from app.persistence.session import SessionFactory
from app.persistence.unit_of_work import UnitOfWork


class FakeSession:
    def __init__(self):
        self.calls = []

    def begin(self):
        self.calls.append("begin")

    def commit(self):
        self.calls.append("commit")

    def rollback(self):
        self.calls.append("rollback")

    def close(self):
        self.calls.append("close")


def test_engine_creation_is_lazy_cached_and_replaceable():
    calls = []
    fake_engine = object()

    def engine_factory(database_url, **kwargs):
        calls.append((database_url, kwargs))
        return fake_engine

    factory = SessionFactory(
        "postgresql://db.example/agent_platform",
        engine_factory=engine_factory,
    )

    assert calls == []
    assert factory.engine is fake_engine
    assert factory.engine is fake_engine
    assert calls == [
        (
            "postgresql+psycopg://db.example/agent_platform",
            {"echo": False, "pool_pre_ping": True},
        )
    ]


def test_default_postgresql_url_selects_installed_psycopg3_driver():
    from sqlalchemy import create_engine

    engine = create_engine("postgresql+psycopg://db.example/agent_platform")

    assert engine.dialect.driver == "psycopg"
    engine.dispose()


def test_session_factory_centralizes_engine_and_creates_separate_sessions():
    fake_engine = object()
    sessions = [FakeSession(), FakeSession()]
    factory_calls = []

    def engine_factory(database_url, **kwargs):
        assert database_url == "postgresql+psycopg://db.example/agent_platform"
        return fake_engine

    def sessionmaker_factory(**kwargs):
        factory_calls.append(kwargs)
        return iter(sessions).__next__

    session_factory = SessionFactory(
        "postgresql://db.example/agent_platform",
        engine_factory=engine_factory,
        sessionmaker_factory=sessionmaker_factory,
    )

    first = session_factory()
    second = session_factory()

    assert first is not second
    assert len(factory_calls) == 1
    assert factory_calls[0] == {
        "bind": fake_engine,
        "class_": Session,
        "expire_on_commit": False,
    }


def test_session_scope_always_closes_its_session():
    fake_session = FakeSession()
    session_factory = SessionFactory(
        "postgresql://db.example/agent_platform",
        engine_factory=lambda *args, **kwargs: object(),
        sessionmaker_factory=lambda **kwargs: lambda: fake_session,
    )

    with session_factory.session_scope() as session:
        assert session is fake_session

    assert fake_session.calls == ["close"]


def test_unit_of_work_commits_and_closes_after_success():
    fake_session = FakeSession()

    with UnitOfWork(lambda: fake_session) as unit_of_work:
        assert unit_of_work.session is fake_session

    assert fake_session.calls == ["begin", "commit", "close"]


def test_unit_of_work_rolls_back_and_closes_after_exception():
    fake_session = FakeSession()

    with pytest.raises(RuntimeError, match="operation failed"):
        with UnitOfWork(lambda: fake_session):
            raise RuntimeError("operation failed")

    assert fake_session.calls == ["begin", "rollback", "close"]


def test_explicit_unit_of_work_rollback_is_not_followed_by_commit():
    fake_session = FakeSession()

    with UnitOfWork(lambda: fake_session) as unit_of_work:
        unit_of_work.rollback()

    assert fake_session.calls == ["begin", "rollback", "close"]


def test_commit_failure_rolls_back_and_closes_session():
    class CommitFailureSession(FakeSession):
        def commit(self):
            self.calls.append("commit")
            raise RuntimeError("commit failed")

    fake_session = CommitFailureSession()

    with pytest.raises(RuntimeError, match="commit failed"):
        with UnitOfWork(lambda: fake_session):
            pass

    assert fake_session.calls == ["begin", "commit", "rollback", "close"]


def test_app_import_and_health_startup_do_not_construct_database_engine():
    repository_root = Path(__file__).resolve().parents[1]
    process_env = os.environ.copy()
    process_env["DATABASE_URL"] = "postgresql://localhost:1/not_a_real_database"
    process_env["REDIS_URL"] = "redis://localhost:1/0"
    script = """
import sqlalchemy
def unexpected_engine_creation(*args, **kwargs):
    raise AssertionError('application startup constructed a database engine')
sqlalchemy.create_engine = unexpected_engine_creation
from fastapi.testclient import TestClient
from app.main import app
assert TestClient(app).get('/health').status_code == 200
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repository_root,
        env=process_env,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr
