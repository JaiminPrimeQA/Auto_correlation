import time

import pytest

from app.domain.execution_job import ExecutionJob, ExecutionJobState
from app.repositories.execution_job_store import InMemoryExecutionJobStore


def _job(id="j1", owner="127.0.0.1", ttl=60, **overrides) -> ExecutionJob:
    base = dict(id=id, owner_key=owner, collection_name="Demo",
                created_at=time.time(), expires_at=time.time() + ttl)
    base.update(overrides)
    return ExecutionJob(**base)


@pytest.fixture
def store():
    return InMemoryExecutionJobStore(ttl_seconds=60)


def test_create_and_get(store):
    job = _job()
    store.create(job)
    assert store.get("j1") is job


def test_get_missing_returns_none(store):
    assert store.get("missing") is None


def test_get_expired_returns_none_and_evicts(store):
    store.create(_job(ttl=-1))
    assert store.get("j1") is None
    assert store.get("j1") is None  # already evicted, still None


def test_update_persists_mutation(store):
    job = _job()
    store.create(job)
    job.state = ExecutionJobState.READY
    store.update(job)
    assert store.get("j1").state == ExecutionJobState.READY


def test_delete(store):
    store.create(_job())
    assert store.delete("j1") is True
    assert store.get("j1") is None
    assert store.delete("j1") is False


def test_find_by_idempotency_key_scoped_to_owner(store):
    store.create(_job(id="a", owner="owner1", idempotency_key="k1"))
    store.create(_job(id="b", owner="owner2", idempotency_key="k1"))
    found = store.find_by_idempotency_key("owner1", "k1", window_seconds=600)
    assert found.id == "a"
    assert store.find_by_idempotency_key("owner2", "k1", window_seconds=600).id == "b"
    assert store.find_by_idempotency_key("owner1", "no-such-key", window_seconds=600) is None


def test_find_by_idempotency_key_returns_terminal_job_within_window(store):
    store.create(_job(id="a", owner="o", idempotency_key="k", state=ExecutionJobState.FAILED))
    found = store.find_by_idempotency_key("o", "k", window_seconds=60)
    assert found is not None
    assert found.id == "a"


def test_find_by_idempotency_key_ignores_jobs_older_than_window(store):
    store.create(_job(
        id="a", owner="o", idempotency_key="k",
        created_at=time.time() - 120, expires_at=time.time() + 60,
    ))
    assert store.find_by_idempotency_key("o", "k", window_seconds=60) is None


def test_count_active_excludes_terminal_states(store):
    store.create(_job(id="a", owner="o", state=ExecutionJobState.QUEUED))
    store.create(_job(id="b", owner="o", state=ExecutionJobState.RUNNING_BASELINE))
    store.create(_job(id="c", owner="o", state=ExecutionJobState.READY))
    store.create(_job(id="d", owner="other", state=ExecutionJobState.QUEUED))
    assert store.count_active("o") == 2
