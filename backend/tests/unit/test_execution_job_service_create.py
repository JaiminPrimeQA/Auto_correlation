import json
import threading

import pytest

from app.core.config import Settings
from app.core.errors import ProblemException
from app.domain.execution_job import ExecutionJobState
from app.repositories.execution_job_store import InMemoryExecutionJobStore
from app.services import execution_job_service
from app.services.execution_job_service import create_job
from app.services.postman_folder_extractor import extract_folders
from tests.fixtures.postman_builders import pm_collection, pm_environment, pm_folder, pm_request


def _collection():
    return pm_collection("Login Flow", [pm_request("Login", "POST", "https://{{host}}/login")])


def _store():
    return InMemoryExecutionJobStore(ttl_seconds=60)


def test_creates_queued_job_when_all_variables_resolved():
    raw = json.dumps(_collection()).encode()
    store = _store()
    job, created = create_job(
        collection_raw=raw, collection_filename="c.json",
        environment_raw=None, environment_filename=None,
        folder_id=None, supplied_values={"host": "93.184.216.34"},
        owner_key="127.0.0.1", idempotency_key=None,
        store=store, settings=Settings(),
    )
    assert created is True
    assert job.state == ExecutionJobState.QUEUED
    assert job.collection_name == "Login Flow"
    assert store.get(job.id) is job


def test_rejects_when_variables_remain_unresolved():
    raw = json.dumps(_collection()).encode()
    with pytest.raises(ProblemException) as exc:
        create_job(
            collection_raw=raw, collection_filename="c.json",
            environment_raw=None, environment_filename=None,
            folder_id=None, supplied_values={},
            owner_key="127.0.0.1", idempotency_key=None,
            store=_store(), settings=Settings(),
        )
    assert exc.value.status == 422
    assert "host" in exc.value.detail


def test_environment_values_satisfy_resolution_without_supplied():
    raw = json.dumps(_collection()).encode()
    env = json.dumps(pm_environment("dev", {"host": "93.184.216.34"})).encode()
    job, created = create_job(
        collection_raw=raw, collection_filename="c.json",
        environment_raw=env, environment_filename="e.json",
        folder_id=None, supplied_values={},
        owner_key="127.0.0.1", idempotency_key=None,
        store=_store(), settings=Settings(),
    )
    assert created is True
    assert job.state == ExecutionJobState.QUEUED


def test_supplied_values_never_appear_on_the_returned_job():
    raw = json.dumps(_collection()).encode()
    job, created = create_job(
        collection_raw=raw, collection_filename="c.json",
        environment_raw=None, environment_filename=None,
        folder_id=None, supplied_values={"host": "93.184.216.34"},
        owner_key="127.0.0.1", idempotency_key=None,
        store=_store(), settings=Settings(),
    )
    assert created is True
    dumped = str(vars(job))
    assert "93.184.216.34" not in dumped


def test_concurrency_limit_enforced_per_owner():
    raw = json.dumps(_collection()).encode()
    store = _store()
    settings = Settings(max_concurrent_jobs_per_owner=1)
    create_job(
        collection_raw=raw, collection_filename="c.json", environment_raw=None, environment_filename=None,
        folder_id=None, supplied_values={"host": "93.184.216.34"}, owner_key="127.0.0.1",
        idempotency_key=None, store=store, settings=settings,
    )
    with pytest.raises(ProblemException) as exc:
        create_job(
            collection_raw=raw, collection_filename="c.json", environment_raw=None, environment_filename=None,
            folder_id=None, supplied_values={"host": "93.184.216.34"}, owner_key="127.0.0.1",
            idempotency_key=None, store=store, settings=settings,
        )
    assert exc.value.status == 429


def test_idempotency_key_returns_existing_active_job_instead_of_creating_a_new_one():
    raw = json.dumps(_collection()).encode()
    store = _store()
    first, first_created = create_job(
        collection_raw=raw, collection_filename="c.json", environment_raw=None, environment_filename=None,
        folder_id=None, supplied_values={"host": "93.184.216.34"}, owner_key="127.0.0.1",
        idempotency_key="key-1", store=store, settings=Settings(),
    )
    second, second_created = create_job(
        collection_raw=raw, collection_filename="c.json", environment_raw=None, environment_filename=None,
        folder_id=None, supplied_values={"host": "93.184.216.34"}, owner_key="127.0.0.1",
        idempotency_key="key-1", store=store, settings=Settings(),
    )
    assert first_created is True
    assert second_created is False
    assert second.id == first.id


def test_unknown_folder_id_returns_422():
    raw = json.dumps(_collection()).encode()
    with pytest.raises(ProblemException) as exc:
        create_job(
            collection_raw=raw, collection_filename="c.json",
            environment_raw=None, environment_filename=None,
            folder_id="does-not-exist", supplied_values={"host": "93.184.216.34"},
            owner_key="127.0.0.1", idempotency_key=None,
            store=_store(), settings=Settings(),
        )
    assert exc.value.status == 422
    assert "does-not-exist" in exc.value.detail


def test_valid_folder_id_succeeds():
    collection = pm_collection(
        "Login Flow", [pm_folder("Auth", [pm_request("Login", "POST", "https://{{host}}/login")])],
    )
    raw = json.dumps(collection).encode()
    folder_id = extract_folders(collection)[0].id
    job, created = create_job(
        collection_raw=raw, collection_filename="c.json",
        environment_raw=None, environment_filename=None,
        folder_id=folder_id, supplied_values={"host": "93.184.216.34"},
        owner_key="127.0.0.1", idempotency_key=None,
        store=_store(), settings=Settings(),
    )
    assert created is True
    assert job.state == ExecutionJobState.QUEUED


# --- F1: atomic create under concurrency ---


def _race_create(monkeypatch, n: int, *, idempotency_keys: list, settings: Settings):
    """Run `n` concurrent create_job calls for the same owner.

    `parse_collection` (called after the non-atomic fast-path checks) is wrapped
    to wait on a Barrier of `n`, so every thread has passed the fast path before
    any thread can reach the insert - the race window is forced open
    deterministically, with no sleeps.
    """
    barrier = threading.Barrier(n, timeout=10)
    real_parse = execution_job_service.parse_collection

    def _parse_after_all_threads_arrive(*args, **kwargs):
        barrier.wait()
        return real_parse(*args, **kwargs)

    monkeypatch.setattr(execution_job_service, "parse_collection", _parse_after_all_threads_arrive)
    raw = json.dumps(_collection()).encode()
    store = _store()
    results: list = [None] * n

    def _worker(i: int) -> None:
        try:
            results[i] = create_job(
                collection_raw=raw, collection_filename="c.json", environment_raw=None, environment_filename=None,
                folder_id=None, supplied_values={"host": "93.184.216.34"}, owner_key="127.0.0.1",
                idempotency_key=idempotency_keys[i], store=store, settings=settings,
            )
        except ProblemException as exc:
            results[i] = exc

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert not any(t.is_alive() for t in threads)
    return store, results


def test_concurrent_same_idempotency_key_creates_exactly_one_job(monkeypatch):
    n = 4
    store, results = _race_create(monkeypatch, n, idempotency_keys=["same"] * n, settings=Settings())
    assert all(isinstance(r, tuple) for r in results)
    assert sum(1 for _, created in results if created) == 1
    assert len({job.id for job, _ in results}) == 1
    assert store.count_active("127.0.0.1") == 1


def test_concurrent_creates_never_exceed_the_active_cap(monkeypatch):
    n = 5
    store, results = _race_create(
        monkeypatch, n, idempotency_keys=[f"k{i}" if i % 2 else None for i in range(n)],
        settings=Settings(max_concurrent_jobs_per_owner=2),
    )
    created = [r for r in results if isinstance(r, tuple)]
    rejected = [r for r in results if isinstance(r, ProblemException)]
    assert len(created) == 2
    assert all(c for _, c in created)
    assert len(rejected) == n - 2
    assert all(e.status == 429 and e.code == "rate_limited" for e in rejected)
    assert store.count_active("127.0.0.1") == 2
