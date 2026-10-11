"""Check the TLS-only Redis endpoint; read its credential-bearing URL from stdin."""
import json
import socket
import ssl
import sys
from urllib.parse import parse_qs, urlsplit

import redis


def verify(url: str) -> dict[str, object]:
    parsed = urlsplit(url)
    if parsed.scheme != "rediss":
        raise RuntimeError("TLS URL required")
    ca_pem = parse_qs(parsed.query)["ssl_ca_data"][0]
    address = (parsed.hostname, parsed.port or 6379)
    with redis.Redis.from_url(url, socket_connect_timeout=5, socket_timeout=5) as client:
        if not client.ping():
            raise RuntimeError("Verified PING failed")

    context = ssl.create_default_context(cadata=ca_pem)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    with socket.create_connection(address, timeout=5) as tcp:
        with context.wrap_socket(tcp, server_hostname=parsed.hostname) as secure:
            expiry = secure.getpeercert()["notAfter"]
            protocol = secure.version()

    # A trusted CA must not permit the wrong hostname.
    try:
        with socket.create_connection(address, timeout=5) as tcp:
            with context.wrap_socket(tcp, server_hostname="wrong-host.invalid"):
                pass
    except ssl.SSLCertVerificationError:
        pass
    else:
        raise RuntimeError("Hostname verification was bypassed")

    # System roots must reject this deployment's private CA.
    try:
        with socket.create_connection(address, timeout=5) as tcp:
            with ssl.create_default_context().wrap_socket(tcp, server_hostname=parsed.hostname):
                pass
    except ssl.SSLCertVerificationError:
        pass
    else:
        raise RuntimeError("Untrusted CA was accepted")

    with socket.create_connection(address, timeout=5) as tcp:
        tcp.settimeout(2)
        try:
            # Never send credentials over plaintext.
            tcp.sendall(b"*1\r\n$4\r\nPING\r\n")
            response = tcp.recv(64)
        except (socket.timeout, ConnectionResetError):
            response = b""
        if response.startswith((b"+PONG", b"-NOAUTH", b"-ERR")):
            raise RuntimeError("Plaintext Redis is still enabled")

    return {"verified_ping": True, "wrong_hostname_rejected": True,
            "untrusted_ca_rejected": True, "plaintext_disabled": True,
            "tls_version": protocol, "certificate_expires": expiry}


if __name__ == "__main__":
    try:
        print(json.dumps(verify(sys.stdin.read().strip())))
    except Exception:
        # Redis errors can contain connection details; never print exceptions.
        print('Redis TLS verification failed; credential details suppressed.', file=sys.stderr)
        sys.exit(1)
