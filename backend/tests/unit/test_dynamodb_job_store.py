"""DynamoDB-specific behaviour on top of the shared store contract
(tests/unit/test_execution_job_store.py runs every contract test against
this store too)."""

import threading
import time

import boto3
import pytest

from app.core.errors import ProblemException
from app.domain.execution_job import ExecutionJob, ExecutionJobState
from tests.fixtures.aws import REGION, TABLE, dynamodb_job_store


def _job(id, owner="o", **overrides) -> ExecutionJob:
    now = time.time()
    base = dict(id=id, owner_key=owner, collection_name="Demo", created_at=now, expires_at=now + 60)
    base.update(overrides)
    return ExecutionJob(**base)


def _raw_items():
    client = boto3.client("dynamodb", region_name=REGION)
    return {item["pk"]["S"]: item for item in client.scan(TableName=TABLE)["Items"]}


def test_round_trips_every_job_field():
    with dynamodb_job_store() as store:
        job = _job("a", idempotency_key="k", warnings=["w1"], stage_history=["validating"],
                   state=ExecutionJobState.RUNNING_BASELINE, error_code="x", error_detail="y",
                   cancel_requested=True, worker_active=True, analysis_id="an")
        store.create(job)
        assert store.get("a") == job


def test_items_carry_a_ttl_after_expiry_and_no_supplied_values():
    with dynamodb_job_store() as store:
        store.create(_job("a"))
        item = _raw_items()["JOB#a"]
        assert int(item["ttl"]["N"]) > time.time()
        assert "supplied" not in str(item)


def test_slot_is_released_when_the_job_stops_counting():
    with dynamodb_job_store() as store:
        store.create_if_allowed(_job("a"), window_seconds=600, max_active=1)
        assert store.count_active("o") == 1

        def _finish(j):
            j.state = ExecutionJobState.READY

        store.mutate("a", _finish)
        assert store.count_active("o") == 0
        store.mutate("a", lambda j: j.warnings.append("again"))  # no double release
        assert store.count_active("o") == 0
        store.create_if_allowed(_job("b"), window_seconds=600, max_active=1)
        assert store.count_active("o") == 1


def test_deleting_an_active_job_releases_its_slot():
    with dynamodb_job_store() as store:
        store.create_if_allowed(_job("a"), window_seconds=600, max_active=1)
        assert store.delete("a") is True
        assert store.count_active("o") == 0


def test_concurrent_submissions_never_exceed_the_cap():
    with dynamodb_job_store() as store:
        results: list[str] = []
        lock = threading.Lock()

        def submit(i):
            try:
                store.create_if_allowed(_job(f"j{i}"), window_seconds=600, max_active=2)
                outcome = "created"
            except ProblemException:
                outcome = "rejected"
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=submit, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert results.count("created") == 2
        assert store.count_active("o") == 2


def test_concurrent_mutations_are_not_lost():
    with dynamodb_job_store() as store:
        store.create(_job("a"))

        def add_warning(i):
            store.mutate("a", lambda j: j.warnings.append(f"w{i}"))

        threads = [threading.Thread(target=add_warning, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(store.get("a").warnings) == sorted(f"w{i}" for i in range(8))


def test_idempotency_record_expires_with_the_window():
    with dynamodb_job_store() as store:
        store.create_if_allowed(_job("a", idempotency_key="k"), window_seconds=600, max_active=5)
        item = _raw_items()["IDEM#o#k"]
        assert int(item["ttl"]["N"]) <= time.time() + 601


@pytest.mark.parametrize("owner", ["user:a#b", "10.0.0.1"])
def test_owner_keys_with_separators_do_not_collide(owner):
    with dynamodb_job_store() as store:
        store.create_if_allowed(_job("a", owner=owner, idempotency_key="x#y"), window_seconds=600, max_active=5)
        assert store.find_by_idempotency_key(owner, "x#y", window_seconds=600).id == "a"
        assert store.find_by_idempotency_key(owner + "#x", "y", window_seconds=600) is None
