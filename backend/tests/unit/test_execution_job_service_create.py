import json

import pytest

from app.core.config import Settings
from app.core.errors import ProblemException
from app.domain.execution_job import ExecutionJobState
from app.repositories.execution_job_store import InMemoryExecutionJobStore
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
