import ssl
from urllib.parse import urlencode

from celery import Celery
from pydantic import ValidationError
import pytest
from redis import Redis, SSLConnection

from app.config import Settings
from app.queue.celery import redis_transport_security


def test_rediss_configures_verified_broker_backend_and_health_client():
    url = "rediss://cache.example:6380/2"
    app = Celery("redis_tls_test", broker=url, backend=url)
    app.conf.update(**redis_transport_security(url))
    try:
        assert app.connection_for_write().ssl == {
            "ssl_cert_reqs": ssl.CERT_REQUIRED, "ssl_check_hostname": True,
        }
        # Constructing Celery's backend used to reject a bare rediss URL.
        backend = app.backend
        assert backend.connparams["connection_class"] is SSLConnection
        assert backend.connparams["ssl_cert_reqs"] == ssl.CERT_REQUIRED
        assert backend.connparams["ssl_check_hostname"] is True
        client = Redis.from_url(url)
        connection = client.connection_pool.connection_class(
            **client.connection_pool.connection_kwargs,
        )
        assert connection.cert_reqs == ssl.CERT_REQUIRED
        assert connection.check_hostname is True
        client.close()
    finally:
        app.close()


def test_plaintext_local_redis_does_not_enable_ssl():
    assert redis_transport_security("redis://localhost:6379/0") == {}


def test_celery_and_health_client_preserve_inline_ca_trust():
    ca = "-----BEGIN CERTIFICATE-----\npublic-ca\n-----END CERTIFICATE-----"
    url = "rediss://cache.example/0?" + urlencode({
        "ssl_ca_data": ca, "ssl_cert_reqs": "required", "ssl_check_hostname": "true",
    })
    app = Celery("redis_custom_ca_test", broker=url, backend=url)
    app.conf.update(**redis_transport_security(url))
    try:
        assert app.backend.connparams["ssl_ca_data"] == ca
        client = Redis.from_url(url)
        assert client.connection_pool.connection_kwargs["ssl_ca_data"] == ca
        assert client.connection_pool.connection_kwargs["ssl_check_hostname"] is True
        client.close()
    finally:
        app.close()


@pytest.mark.parametrize("query", [
    "ssl_cert_reqs=none", "ssl_cert_reqs=optional", "ssl_cert_reqs=",
    "ssl_check_hostname=false", "ssl_check_hostname=0", "ssl_check_hostname=",
    "ssl_cert_reqs=none&ssl_cert_reqs=required",
])
def test_rediss_rejects_url_overrides_that_disable_verification(query):
    with pytest.raises(ValidationError, match="Redis TLS must"):
        Settings(_env_file=None, redis_url=f"rediss://cache.example/0?{query}")


def test_rediss_allows_required_verification_and_a_custom_ca():
    url = ("rediss://cache.example/0?ssl_cert_reqs=required"
           "&ssl_check_hostname=true&ssl_ca_certs=%2Frun%2Fsecrets%2Fredis-ca.pem")
    assert Settings(_env_file=None, redis_url=url).redis_url == url
