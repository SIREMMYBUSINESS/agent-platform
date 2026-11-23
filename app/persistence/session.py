"""Lazy database engine and centralized SQLModel session factory."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import RLock

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session

from app.config import settings


DatabaseUrlProvider = str | Callable[[], str]
EngineFactory = Callable[..., Engine]
SessionMakerFactory = Callable[..., sessionmaker[Session]]


class SessionFactory:
    """Lazily owns one engine and creates independently owned sessions."""

    def __init__(
        self,
        database_url: DatabaseUrlProvider = lambda: settings.database_url,
        *,
        engine_factory: EngineFactory = create_engine,
        sessionmaker_factory: SessionMakerFactory = sessionmaker,
    ) -> None:
        self._database_url = database_url
        self._engine_factory = engine_factory
        self._sessionmaker_factory = sessionmaker_factory
        self._engine: Engine | None = None
        self._sessionmaker: sessionmaker[Session] | None = None
        self._creation_lock = RLock()

    @property
    def engine(self) -> Engine:
        """Create the engine on first database use; engine creation does not connect."""
        if self._engine is None:
            with self._creation_lock:
                if self._engine is None:
                    database_url = (
                        self._database_url() if callable(self._database_url) else self._database_url
                    )
                    # The project installs psycopg 3. SQLAlchemy's bare
                    # postgresql:// default still selects the psycopg2 driver.
                    if database_url.startswith("postgresql://"):
                        database_url = database_url.replace(
                            "postgresql://", "postgresql+psycopg://", 1
                        )
                    self._engine = self._engine_factory(
                        database_url,
                        echo=False,
                        pool_pre_ping=True,
                    )
        return self._engine

    def __call__(self) -> Session:
        """Create a new session; the caller owns and must close the returned session."""
        if self._sessionmaker is None:
            with self._creation_lock:
                if self._sessionmaker is None:
                    self._sessionmaker = self._sessionmaker_factory(
                        bind=self.engine,
                        class_=Session,
                        expire_on_commit=False,
                    )
        return self._sessionmaker()

    @contextmanager
    def session_scope(self) -> Iterator[Session]:
        """Create and reliably close a non-transaction-owning session."""
        session = self()
        try:
            yield session
        finally:
            session.close()


# This singleton owns only the lazily-created engine and sessionmaker, never a Session.
session_factory = SessionFactory()
control_session_factory = SessionFactory(
    lambda: settings.control_database_url or _missing_control_database_url(),
)


def _missing_control_database_url() -> str:
    raise RuntimeError("CONTROL_DATABASE_URL is required when tenant region routing is enabled")
