import pytest

from app.workers.railway_entry import _worker_args


def test_railway_worker_uses_only_selected_domain_and_approved_region():
    args = _worker_args({
        "DEPLOYMENT_REGION_ID": "eu-west-1",
        "WORKER_DOMAINS": "inbound",
        "WORKER_CONCURRENCY": "3",
    })

    assert "--concurrency=3" in args
    assert "--queues=agent.control,agent.eu-west-1.inbound,agent.inbound" in args


def test_railway_worker_rejects_unknown_domain_and_region():
    with pytest.raises(ValueError, match="WORKER_DOMAINS"):
        _worker_args({"WORKER_DOMAINS": "customers"})
    with pytest.raises(ValueError, match="unsupported deployment region"):
        _worker_args({"DEPLOYMENT_REGION_ID": "unknown-region", "WORKER_DOMAINS": "inbound"})


def test_railway_worker_validates_concurrency():
    with pytest.raises(ValueError, match="positive integer"):
        _worker_args({"WORKER_DOMAINS": "inbound", "WORKER_CONCURRENCY": "many"})
    with pytest.raises(ValueError, match="between 1 and 64"):
        _worker_args({"WORKER_DOMAINS": "inbound", "WORKER_CONCURRENCY": "0"})
