import time
from unittest.mock import Mock

import pytest

from app.api.deps import get_newman_runner, get_owner_key, require_owned_job
from app.core.config import Settings
from app.core.errors import ProblemException
from app.domain.execution_job import ExecutionJob
from app.repositories.execution_job_store import InMemoryExecutionJobStore
from app.services.fake_newman_runner import FakeNewmanRunner


def _request(host: str | None):
    req = Mock()
    req.client = Mock(host=host) if host else None
    return req


def test_get_owner_key_uses_client_host():
    assert get_owner_key(_request("10.0.0.5")) == "10.0.0.5"


def test_get_owner_key_falls_back_when_no_client():
    assert get_owner_key(_request(None)) == "unknown"


def test_require_owned_job_returns_job_for_matching_owner():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = ExecutionJob(id="j1", owner_key="10.0.0.5", collection_name="d",
                        created_at=time.time(), expires_at=time.time() + 60)
    store.create(job)
    result = require_owned_job("j1", _request("10.0.0.5"), store)
    assert result is job


def test_require_owned_job_404s_for_missing_job():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    with pytest.raises(ProblemException) as exc:
        require_owned_job("missing", _request("10.0.0.5"), store)
    assert exc.value.status == 404


def test_get_newman_runner_is_none_when_disabled():
    assert get_newman_runner(Settings(newman_runner="disabled")) is None


def test_get_newman_runner_returns_a_fresh_canned_fake_when_configured():
    first = get_newman_runner(Settings(newman_runner="fake"))
    second = get_newman_runner(Settings(newman_runner="fake"))
    assert isinstance(first, FakeNewmanRunner)
    assert first is not second  # one runner per request: outcomes are consumed per job
    assert len(first.outcomes) == 2
    assert all(o.success and o.report_bytes for o in first.outcomes)


def test_require_owned_job_404s_for_mismatched_owner_not_403():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = ExecutionJob(id="j1", owner_key="10.0.0.5", collection_name="d",
                        created_at=time.time(), expires_at=time.time() + 60)
    store.create(job)
    with pytest.raises(ProblemException) as exc:
        require_owned_job("j1", _request("10.0.0.9"), store)
    assert exc.value.status == 404
