import time

import pytest

from app.core.errors import ProblemException
from app.domain.execution_job import ExecutionJob, ExecutionJobState
from app.repositories.execution_job_store import InMemoryExecutionJobStore
from tests.fixtures.aws import dynamodb_job_store


def _job(id="j1", owner="127.0.0.1", ttl=60, **overrides) -> ExecutionJob:
    base = dict(id=id, owner_key=owner, collection_name="Demo",
                created_at=time.time(), expires_at=time.time() + ttl)
    base.update(overrides)
    return ExecutionJob(**base)


@pytest.fixture(params=["memory", "dynamodb"])
def store(request):
    # Every contract below must hold for both the in-memory store (local
    # development) and the DynamoDB store (AWS backend, via moto).
    if request.param == "memory":
        yield InMemoryExecutionJobStore(ttl_seconds=60)
    else:
        with dynamodb_job_store() as dynamo:
            yield dynamo


def test_create_and_get(store):
    job = _job()
    store.create(job)
    assert store.get("j1") == job


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


def test_mutate_missing_job_returns_none_and_inserts_nothing(store):
    calls = []
    result = store.mutate("missing", lambda job: calls.append(job))
    assert result is None
    assert calls == []
    assert store.get("missing") is None


def test_mutate_applies_the_function_and_persists_the_mutation(store):
    job = _job()
    store.create(job)

    def _bump(j: ExecutionJob) -> None:
        j.state = ExecutionJobState.READY

    result = store.mutate("j1", _bump)
    assert (result.id, result.state) == ("j1", ExecutionJobState.READY)
    assert store.get("j1").state == ExecutionJobState.READY


def test_mutate_expired_job_returns_none_and_evicts_it(store):
    store.create(_job(ttl=-1))
    result = store.mutate("j1", lambda j: None)
    assert result is None
    assert store.get("j1") is None


# --- F1: most-recent tie-break + atomic create_if_allowed ---


def test_find_by_idempotency_key_returns_the_most_recent_match(store):
    now = time.time()
    store.create(_job(id="older", owner="o", idempotency_key="k", created_at=now - 30))
    store.create(_job(id="newest", owner="o", idempotency_key="k", created_at=now - 1))
    store.create(_job(id="middle", owner="o", idempotency_key="k", created_at=now - 10))
    assert store.find_by_idempotency_key("o", "k", window_seconds=600).id == "newest"


def test_create_if_allowed_inserts_when_under_cap(store):
    job = _job(id="a", owner="o")
    result, created = store.create_if_allowed(job, window_seconds=600, max_active=2)
    assert created is True
    assert result == job
    assert store.get("a") == job


def test_create_if_allowed_returns_existing_on_idempotency_hit(store):
    existing = _job(id="a", owner="o", idempotency_key="k")
    store.create(existing)
    result, created = store.create_if_allowed(
        _job(id="b", owner="o", idempotency_key="k"), window_seconds=600, max_active=2,
    )
    assert created is False
    assert result == existing
    assert store.get("b") is None


def test_create_if_allowed_idempotency_hit_wins_over_cap(store):
    store.create(_job(id="a", owner="o", idempotency_key="k"))
    store.create(_job(id="b", owner="o"))
    result, created = store.create_if_allowed(
        _job(id="c", owner="o", idempotency_key="k"), window_seconds=600, max_active=2,
    )
    assert (result.id, created) == ("a", False)


def test_create_if_allowed_raises_429_at_cap_and_inserts_nothing(store):
    store.create(_job(id="a", owner="o"))
    store.create(_job(id="b", owner="o"))
    with pytest.raises(ProblemException) as exc:
        store.create_if_allowed(_job(id="c", owner="o"), window_seconds=600, max_active=2)
    assert exc.value.status == 429
    assert exc.value.code == "rate_limited"
    assert store.get("c") is None


# --- F4(b): a live worker counts against the cap even after the job is terminal ---


def test_count_active_counts_a_terminal_job_whose_worker_is_still_running(store):
    store.create(_job(id="a", owner="o", state=ExecutionJobState.CANCELLED, worker_active=True))
    store.create(_job(id="b", owner="o", state=ExecutionJobState.CANCELLED, worker_active=False))
    assert store.count_active("o") == 1


def test_create_if_allowed_cap_counts_live_workers(store):
    store.create(_job(id="a", owner="o", state=ExecutionJobState.CANCELLED, worker_active=True))
    with pytest.raises(ProblemException) as exc:
        store.create_if_allowed(_job(id="b", owner="o"), window_seconds=600, max_active=1)
    assert exc.value.status == 429


# --- F4(c): TTL cleanup never evicts a job with a live worker ---


def test_expired_job_with_a_live_worker_is_never_evicted(store):
    store.create(_job(id="a", owner="o", ttl=-1, worker_active=True))
    store.create(_job(id="b", owner="o", ttl=-1))  # triggers nothing special; plain expired job
    assert store.get("b") is None  # plain expired job is evicted (and cleanup ran)
    assert store.get("a") is not None
    assert store.count_active("o") == 1
    assert store.mutate("a", lambda j: None) is not None


def test_expired_job_is_evicted_once_its_worker_finishes(store):
    store.create(_job(id="a", owner="o", ttl=-1, worker_active=True))

    def _finish(j: ExecutionJob) -> None:
        j.worker_active = False

    assert store.mutate("a", _finish) is not None
    assert store.get("a") is None
    assert store.count_active("o") == 0
