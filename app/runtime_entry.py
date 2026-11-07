"""Start production processes after moving injected database secrets to files."""

from __future__ import annotations

import base64
import binascii
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


def materialize_control_database_url() -> None:
    """Move an injected control-database URL into a private read-only file."""
    value = os.environ.pop("CONTROL_DATABASE_URL", None)
    if not value or os.environ.get("CONTROL_DATABASE_URL_FILE"):
        return
    fd, path = tempfile.mkstemp(
        prefix="agent-platform-control-", suffix=".secret",
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
    os.environ["CONTROL_DATABASE_URL_FILE"] = path


def materialize_database_ssl_root_cert() -> None:
    """Write the Railway PostgreSQL CA certificate for libpq verification."""
    encoded = os.environ.pop("DATABASE_SSL_ROOT_CERT_B64", None)
    if not encoded:
        return
    encoded = encoded.strip()
    try:
        certificate = base64.b64decode(encoded, validate=True)
        text = certificate.decode("ascii")
        if "-----BEGIN CERTIFICATE-----" not in text or "-----END CERTIFICATE-----" not in text:
            raise ValueError
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise RuntimeError("DATABASE_SSL_ROOT_CERT_B64 is invalid") from None

    fd, path = tempfile.mkstemp(
        prefix="agent-platform-db-ca-", suffix=".crt",
        dir=os.environ.get("TMPDIR", "/tmp"),
    )
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "wb") as certificate_file:
            certificate_file.write(certificate)
            certificate_file.flush()
            os.fsync(certificate_file.fileno())
        os.chmod(path, stat.S_IRUSR)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        Path(path).unlink(missing_ok=True)
        raise
    os.environ["PGSSLROOTCERT"] = path


def main() -> None:
    materialize_database_url()
    materialize_control_database_url()
    materialize_database_ssl_root_cert()
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
    if process_type == "staging-queue-smoke":
        from app.staging_queue_smoke import run

        run()
        return
    if process_type == "provision-api-key":
        from app.admin.provision_api_key import main as provision_api_key

        raise SystemExit(provision_api_key(sys.argv[2:]))
    raise SystemExit(
        "usage: python -m app.runtime_entry [api|worker|migrate|staging-queue-smoke|provision-api-key]"
    )


if __name__ == "__main__":
    main()
