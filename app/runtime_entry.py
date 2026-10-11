"""Start production processes after moving injected database secrets to files."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import sys
import tempfile


def materialize_database_url() -> None:
    """Replace an injected DATABASE_URL with a private read-only file path."""
    value = os.environ.pop("DATABASE_URL", None)
    if not value or os.environ.get("DATABASE_URL_FILE"):
        return
    fd, path = tempfile.mkstemp(
        prefix="agent-platform-", suffix=".secret",
        dir=os.environ.get("TMPDIR", "/tmp"),
    )
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as secret_file:
            secret_file.write(value)
            secret_file.flush()
            os.fsync(secret_file.fileno())
        os.chmod(path, stat.S_IRUSR)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        Path(path).unlink(missing_ok=True)
        raise
    os.environ["DATABASE_URL_FILE"] = path


def main() -> None:
    materialize_database_url()
    process_type = sys.argv[1] if len(sys.argv) > 1 else "api"
    if process_type == "api":
        import uvicorn

        uvicorn.run(
            "app.main:app", host="0.0.0.0",
            port=int(os.environ.get("PORT", "8000")),
        )
        return
    if process_type == "worker":
        from app.workers.scheduler import run_worker

        run_worker()
        return
    if process_type == "migrate":
        from alembic import command
        from alembic.config import Config

        command.upgrade(Config("alembic.ini"), "head")
        return
    raise SystemExit("usage: python -m app.runtime_entry [api|worker|migrate]")


if __name__ == "__main__":
    main()
