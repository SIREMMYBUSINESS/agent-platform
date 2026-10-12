import base64
import os
from pathlib import Path

import pytest

from app.runtime_entry import materialize_database_ssl_root_cert


def test_materialize_database_ca_for_libpq(monkeypatch, tmp_path):
    certificate = b"-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----\n"
    monkeypatch.setenv(
        "DATABASE_SSL_ROOT_CERT_B64",
        base64.b64encode(certificate).decode("ascii") + "\n",
    )
    monkeypatch.delenv("PGSSLROOTCERT", raising=False)
    monkeypatch.setenv("TMPDIR", str(tmp_path))

    materialize_database_ssl_root_cert()

    certificate_path = Path(os.environ["PGSSLROOTCERT"])
    assert certificate_path.read_bytes() == certificate
    assert "DATABASE_SSL_ROOT_CERT_B64" not in os.environ


def test_rejects_invalid_database_ca(monkeypatch):
    monkeypatch.setenv("DATABASE_SSL_ROOT_CERT_B64", "not-a-certificate")

    with pytest.raises(RuntimeError, match="DATABASE_SSL_ROOT_CERT_B64 is invalid"):
        materialize_database_ssl_root_cert()
