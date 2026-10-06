"""Shared tenant-scoped configuration, HTTP, and audit contracts for adapters."""

from __future__ import annotations

import json
import hashlib
import os
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ProviderConfigurationError(RuntimeError):
    pass


class ProviderHTTPError(RuntimeError):
    def __init__(self, status_code: int, *, retryable: bool) -> None:
        super().__init__(f"provider_http_error:{status_code}")
        self.status_code = status_code
        self.retryable = retryable


@dataclass(frozen=True)
class ProviderAuditEvent:
    tenant_id: str
    business_id: str
    provider: str
    operation: str
    idempotency_key: str
    outcome: str
    error_code: str | None = None
    occurred_at: datetime = datetime.min.replace(tzinfo=UTC)


class ProviderAuditSink(Protocol):
    def emit(self, event: ProviderAuditEvent) -> None: ...


class TenantConfigResolver(Protocol):
    def resolve(self, *, tenant_id: str, business_id: str) -> Mapping[str, str]: ...


class MappingTenantConfig:
    """Explicit per-tenant/business configuration; missing scopes fail closed."""

    def __init__(self, values: Mapping[tuple[str, str], Mapping[str, str]]) -> None:
        self._values = {key: dict(value) for key, value in values.items()}

    def resolve(self, *, tenant_id: str, business_id: str) -> Mapping[str, str]:
        if not tenant_id.strip() or not business_id.strip():
            raise ProviderConfigurationError("provider scope is required")
        value = self._values.get((tenant_id, business_id))
        if value is None:
            raise ProviderConfigurationError("provider configuration is unavailable for tenant scope")
        return dict(value)


class DirectoryTenantConfigResolver:
    """Resolve tenant credentials from read-only, scope-hashed mounted secret files.

    A file is named by SHA-256 of the tenant/business scope, never by caller text.
    The contents are a JSON object of string configuration values. It is intended
    for a secret volume (for example, a Kubernetes projected Secret volume).
    """

    def __init__(self, root: str | Path, *, max_bytes: int = 65536) -> None:
        try:
            self._root = Path(root).resolve(strict=True)
        except OSError:
            raise ProviderConfigurationError("provider secret directory is unavailable") from None
        if not self._root.is_dir() or max_bytes < 1:
            raise ProviderConfigurationError("provider secret directory is unavailable")
        self._max_bytes = max_bytes

    def resolve(self, *, tenant_id: str, business_id: str) -> Mapping[str, str]:
        if not tenant_id.strip() or not business_id.strip():
            raise ProviderConfigurationError("provider scope is required")
        scope = hashlib.sha256(f"{tenant_id}\0{business_id}".encode()).hexdigest()
        path = self._root / f"{scope}.json"
        try:
            if path.is_symlink():
                raise ProviderConfigurationError("provider configuration is unavailable")
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > self._max_bytes:
                    raise ProviderConfigurationError("provider configuration is unavailable")
                if os.name != "nt":
                    mode = stat.S_IMODE(metadata.st_mode)
                    groups = set(os.getgroups()) | {os.getgid()}
                    if (mode & 0o007 or mode & 0o222
                            or not mode & 0o400 and not (mode & 0o040 and metadata.st_gid in groups)):
                        raise ProviderConfigurationError("provider secret file permissions are too broad")
                with os.fdopen(descriptor, "rb", closefd=False) as secret_file:
                    raw = secret_file.read(self._max_bytes + 1)
            finally:
                os.close(descriptor)
        except ProviderConfigurationError:
            raise
        except OSError:
            raise ProviderConfigurationError("provider configuration is unavailable") from None
        if len(raw) > self._max_bytes:
            raise ProviderConfigurationError("provider configuration is unavailable")
        try:
            values = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProviderConfigurationError("provider configuration is invalid") from None
        if (not isinstance(values, dict) or not values
                or any(not isinstance(key, str) or not isinstance(value, str)
                       for key, value in values.items())):
            raise ProviderConfigurationError("provider configuration is invalid")
        return values


class JSONHTTPTransport(Protocol):
    def request(self, method: str, url: str, *, headers: Mapping[str, str],
                body: bytes | None = None, timeout: float = 10.0) -> tuple[int, Mapping[str, Any]]: ...


class UrllibJSONTransport:
    """Small synchronous JSON transport with sanitized errors."""

    def request(self, method: str, url: str, *, headers: Mapping[str, str],
                body: bytes | None = None, timeout: float = 10.0) -> tuple[int, Mapping[str, Any]]:
        request = Request(url, data=body, headers=dict(headers), method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read()
                decoded = dict(self._decode(raw))
                provider_id = response.headers.get("X-Message-Id")
                if provider_id:
                    decoded["provider_response_headers"] = {"x-message-id": provider_id}
                return response.status, decoded
        except HTTPError as exc:
            retryable = exc.code == 429 or 500 <= exc.code <= 599
            raise ProviderHTTPError(exc.code, retryable=retryable) from None
        except (TimeoutError, URLError) as exc:
            raise ProviderHTTPError(503, retryable=True) from None

    @staticmethod
    def _decode(raw: bytes) -> Mapping[str, Any]:
        if not raw:
            return {}
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, Mapping) else {}


class CallableAuditSink:
    def __init__(self, callback: Callable[[ProviderAuditEvent], None]) -> None:
        self._callback = callback

    def emit(self, event: ProviderAuditEvent) -> None:
        self._callback(event)


def emit_audit(sink: ProviderAuditSink, *, tenant_id: str, business_id: str,
               provider: str, operation: str, idempotency_key: str,
               outcome: str, error_code: str | None = None) -> None:
    sink.emit(ProviderAuditEvent(
        tenant_id=tenant_id, business_id=business_id, provider=provider,
        operation=operation, idempotency_key=idempotency_key,
        outcome=outcome, error_code=error_code, occurred_at=datetime.now(UTC),
    ))


def json_request(transport: JSONHTTPTransport, method: str, url: str, *,
                headers: Mapping[str, str], payload: Mapping[str, Any] | None = None,
                timeout: float = 10.0) -> Mapping[str, Any]:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8") if payload is not None else None
    status, response = transport.request(
        method, url, headers={"Content-Type": "application/json", **headers},
        body=body, timeout=timeout,
    )
    if not 200 <= status < 300:
        raise ProviderHTTPError(status, retryable=status == 429 or status >= 500)
    return response


__all__ = [
    "CallableAuditSink", "DirectoryTenantConfigResolver", "JSONHTTPTransport", "MappingTenantConfig",
    "ProviderAuditEvent", "ProviderAuditSink", "ProviderConfigurationError",
    "ProviderHTTPError", "TenantConfigResolver", "UrllibJSONTransport", "emit_audit",
    "json_request",
]
