"""Heartbeats, ECS task protection and stale-job reconciliation."""

import io
import json
import time
from copy import deepcopy

import boto3
import pytest

from app.core.config import Settings
from app.domain.execution_job import ExecutionJob, ExecutionJobState, RunOutcome
from app.repositories.execution_job_store import InMemoryExecutionJobStore
from app.repositories.s3_object_store import S3ObjectStore, material_key
from app.services.fake_newman_runner import FakeNewmanRunner
from app.services.job_launcher import RunMaterial
from app.services.job_liveness import Heartbeat, TaskProtection, reconcile_stale, stale_reason
from app.services.job_queue import SqsJobQueue
from app.worker import Worker
from tests.fixtures.aws import BUCKET, REGION, aws_mocks
from tests.fixtures.postman_builders import pm_collection, pm_request

SETTINGS = Settings(job_heartbeat_timeout_seconds=180, job_queue_timeout_seconds=900)


def _job(state=ExecutionJobState.RUNNING_BASELINE, *, age=0.0, heartbeat_age=None, **fields):
    now = time.time()
    return ExecutionJob(
        id="job_1", owner_key="user:a", collection_name="Flow", created_at=now - age, expires_at=now + 3600,
        state=state, heartbeat_at=None if heartbeat_age is None else now - heartbeat_age, **fields,
    )


@pytest.mark.parametrize("job,reason", [
    (_job(heartbeat_age=10), None),
    (_job(heartbeat_age=600), "worker_lost"),
    (_job(age=600), "worker_lost"),  # started but never heartbeated
    (_job(ExecutionJobState.QUEUED, age=60), None),
    (_job(ExecutionJobState.QUEUED, age=3600), "queue_timeout"),
    (_job(ExecutionJobState.READY, age=3600), None),
    (_job(ExecutionJobState.ANALYZING, age=3600, reports_ready=True), None),
])
def test_stale_reason(job, reason):
    got = stale_reason(job, SETTINGS, now=time.time())
    assert (got[0] if got else None) == reason


def test_reconcile_fails_a_lost_job_with_a_clear_message():
    store = InMemoryExecutionJobStore(ttl_seconds=3600)
    store.create(_job(heartbeat_age=900, worker_active=True))
    assert reconcile_stale(store.get("job_1"), store, SETTINGS, now=time.time()) is True
    job = store.get("job_1")
    assert (job.state, job.error_code, job.worker_active) == (ExecutionJobState.FAILED, "worker_lost", False)
    assert "Run the job again" in job.error_detail


def test_reconcile_does_not_fail_a_job_that_heartbeated_after_the_read():
    store = InMemoryExecutionJobStore(ttl_seconds=3600)
    store.create(_job(heartbeat_age=900, worker_active=True))
    stale = deepcopy(store.get("job_1"))
    store.mutate("job_1", lambda job: setattr(job, "heartbeat_at", time.time()))
    assert reconcile_stale(stale, store, SETTINGS, now=time.time()) is False
    job = store.get("job_1")
    assert job.state == ExecutionJobState.RUNNING_BASELINE
    assert job.worker_active is True


def test_heartbeat_stamps_the_job_and_extends_visibility():
    store = InMemoryExecutionJobStore(ttl_seconds=3600)
    store.create(_job())
    extended: list[int] = []
    with Heartbeat(job_id="job_1", store=store, extend_visibility=extended.append, interval=0.05,
                   visibility_timeout=1200) as hb:
        time.sleep(0.3)
    assert hb.beats >= 3 and set(extended) == {1200}
    assert store.get("job_1").heartbeat_at is not None


def test_heartbeat_survives_a_failing_extension():
    store = InMemoryExecutionJobStore(ttl_seconds=3600)
    store.create(_job())

    def boom(_):
        raise RuntimeError("sqs down")

    hb = Heartbeat(job_id="job_1", store=store, extend_visibility=boom, interval=1, visibility_timeout=60)
    hb.beat()
    assert hb.beats == 1 and store.get("job_1").heartbeat_at is not None


class _Opener:
    def __init__(self):
        self.calls = []

    def __call__(self, request, timeout):
        self.calls.append((request.full_url, request.get_method(), json.loads(request.data)))
        return io.BytesIO(b"{}")


def test_task_protection_is_requested_and_released():
    opener = _Opener()
    with TaskProtection(expires_in_minutes=40, agent_uri="http://169.254.170.2/api/x", opener=opener):
        pass
    assert opener.calls == [
        ("http://169.254.170.2/api/x/task-protection/v1/state", "PUT",
         {"ProtectionEnabled": True, "ExpiresInMinutes": 40}),
        ("http://169.254.170.2/api/x/task-protection/v1/state", "PUT", {"ProtectionEnabled": False}),
    ]


def test_task_protection_is_a_no_op_outside_ecs():
    opener = _Opener()
    with TaskProtection(expires_in_minutes=10, agent_uri="", opener=opener):
        pass
    assert opener.calls == []


@pytest.fixture
def aws_env():
    with aws_mocks():
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
        sqs = boto3.client("sqs", region_name=REGION)
        url = sqs.create_queue(QueueName="b11-jobs")["QueueUrl"]
        yield {
            "objects": S3ObjectStore(client=s3, bucket=BUCKET, kms_key_id=None),
            "queue": SqsJobQueue(client=sqs, queue_url=url),
            "store": InMemoryExecutionJobStore(ttl_seconds=600),
        }


def test_redelivery_while_the_worker_is_still_alive_does_not_fail_the_job(aws_env):
    store = aws_env["store"]
    store.create(_job(heartbeat_age=5, worker_active=True, stage_history=["validating", "running_baseline"]))
    aws_env["queue"].enqueue("job_1")
    worker = Worker(settings=SETTINGS, queue=aws_env["queue"], job_store=store, objects=aws_env["objects"],
                    runner=FakeNewmanRunner([]), receive_wait_seconds=0,
                    protection=TaskProtection(expires_in_minutes=1, agent_uri=""))
    worker.process_one()
    assert store.get("job_1").state == ExecutionJobState.RUNNING_BASELINE


def test_worker_heartbeats_while_running_a_job(aws_env):
    store = aws_env["store"]
    now = time.time()
    store.create(ExecutionJob(id="job_1", owner_key="user:a", collection_name="Flow", created_at=now,
                              expires_at=now + 600))
    material = RunMaterial(collection_data=pm_collection("Flow", [pm_request("A", "GET", "https://93.184.216.34/")]),
                           environment_data=None, variable_values={}, supplied_values={}, folder_id=None)
    aws_env["objects"].put_json(material_key("job_1"), material.to_document())
    aws_env["queue"].enqueue("job_1")
    opener = _Opener()
    worker = Worker(settings=SETTINGS, queue=aws_env["queue"], job_store=store, objects=aws_env["objects"],
                    runner=FakeNewmanRunner([RunOutcome(False, error_code="timeout", error_detail="t")]),
                    receive_wait_seconds=0,
                    protection=TaskProtection(expires_in_minutes=5, agent_uri="http://agent", opener=opener))
    worker.process_one()
    job = store.get("job_1")
    assert job.heartbeat_at is not None
    assert [c[2]["ProtectionEnabled"] for c in opener.calls] == [True, False]
