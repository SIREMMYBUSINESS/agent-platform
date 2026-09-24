"""Compatibility facade over the canonical lazy persistence session factory.

Production schema changes are managed by Alembic. This module neither creates
tables nor constructs an engine until ``get_engine`` or ``get_session`` is used.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.persistence.session import session_factory


def get_engine() -> Engine:
    """Return the shared lazily-created engine for compatibility callers."""
    return session_factory.engine


@contextmanager
def get_session() -> Iterator[Session]:
    """Yield a session and close it deterministically when the scope exits."""
    with session_factory.session_scope() as session:
        yield session
