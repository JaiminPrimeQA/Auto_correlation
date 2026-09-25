import json
import time

import boto3
import pytest

from app.core.config import Settings
from app.domain.execution_job import ExecutionJob, ExecutionJobState, RunOutcome
from app.repositories.execution_job_store import InMemoryExecutionJobStore
from app.repositories.s3_object_store import S3ObjectStore, material_key, report_key
from app.services.fake_newman_runner import FakeNewmanRunner
from app.services.job_launcher import RunMaterial
from app.services.job_queue import SqsJobQueue
from app.worker import Worker
from tests.fixtures import builders as b
from tests.fixtures.aws import BUCKET, REGION, aws_mocks
from tests.fixtures.postman_builders import pm_collection, pm_request


def _report(token: str) -> bytes:
    return json.dumps(b.report("Flow", [
        b.execution("Login", "POST", "https://api.example.com/login", resp_body={"token": token}, position=0),
        b.execution("Me", "GET", "https://api.example.com/me",
                    req_headers=[b.header("Authorization", f"Bearer {token}")], position=1),
    ])).encode()


_MATERIAL = RunMaterial(
    collection_data=pm_collection("Flow", [pm_request("Login", "POST", "https://93.184.216.34/login")]),
    environment_data=None, variable_values={}, supplied_values={}, folder_id=None,
)


@pytest.fixture
def env():
    with aws_mocks():
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
        sqs = boto3.client("sqs", region_name=REGION)
        queue_url = sqs.create_queue(QueueName="b11-jobs")["QueueUrl"]
        yield {
            "s3": s3,
            "objects": S3ObjectStore(client=s3, bucket=BUCKET, kms_key_id=None),
            "queue": SqsJobQueue(client=sqs, queue_url=queue_url),
            "store": InMemoryExecutionJobStore(ttl_seconds=600),
        }


def _enqueue(env, job_id="job_1", state=ExecutionJobState.QUEUED, **fields):
    now = time.time()
    job = ExecutionJob(id=job_id, owner_key="user:a", collection_name="Flow", created_at=now,
                       expires_at=now + 600, state=state, **fields)
    env["store"].create(job)
    env["objects"].put_json(material_key(job_id), _MATERIAL.to_document())
    env["queue"].enqueue(job_id)
    return job


def _worker(env, runner):
    return Worker(settings=Settings(), queue=env["queue"], job_store=env["store"], objects=env["objects"],
                  runner=runner, receive_wait_seconds=0)


def _keys(env):
    return sorted(o["Key"] for o in env["s3"].list_objects_v2(Bucket=BUCKET).get("Contents", []))


def test_runs_both_stages_and_hands_the_reports_to_the_api(env):
    _enqueue(env)
    runner = FakeNewmanRunner([RunOutcome(True, _report("tok_A1b2C3")), RunOutcome(True, _report("tok_Z9y8X7"))])
    assert _worker(env, runner).process_one() is True
    job = env["store"].get("job_1")
    assert job.state == ExecutionJobState.ANALYZING
    assert job.reports_ready is True
    assert job.worker_active is False
    assert job.analysis_id is None  # the API builds the analysis
    assert _keys(env) == [report_key("job_1", "baseline"), report_key("job_1", "comparison")]
    assert env["objects"].get_bytes(report_key("job_1", "comparison"), max_bytes=10**6) == _report("tok_Z9y8X7")
    assert [c.job_id for c in runner.calls] == ["job_1", "job_1"]
    assert env["queue"].receive(wait_seconds=0, visibility_timeout=1) == []  # message deleted


def test_failed_run_ends_failed_and_leaves_no_material(env):
    _enqueue(env)
    runner = FakeNewmanRunner([RunOutcome(False, error_code="timeout", error_detail="The run timed out.")])
    _worker(env, runner).process_one()
    job = env["store"].get("job_1")
    assert (job.state, job.error_code) == (ExecutionJobState.FAILED, "timeout")
    assert _keys(env) == []


def test_empty_queue_returns_false(env):
    assert _worker(env, FakeNewmanRunner([])).process_one() is False


def test_message_for_a_deleted_job_is_discarded(env):
    _enqueue(env)
    env["store"].delete("job_1")
    runner = FakeNewmanRunner([])
    assert _worker(env, runner).process_one() is True
    assert runner.calls == []
    assert _keys(env) == []


def test_missing_material_fails_the_job(env):
    _enqueue(env)
    env["objects"].delete(material_key("job_1"))
    _worker(env, FakeNewmanRunner([])).process_one()
    job = env["store"].get("job_1")
    assert (job.state, job.error_code) == (ExecutionJobState.FAILED, "runner_unavailable")


def test_redelivered_job_that_a_crashed_worker_started_is_failed_not_rerun(env):
    _enqueue(env, state=ExecutionJobState.RUNNING_BASELINE, worker_active=True,
             stage_history=["validating", "running_baseline"])
    runner = FakeNewmanRunner([])
    _worker(env, runner).process_one()
    job = env["store"].get("job_1")
    assert (job.state, job.error_code) == (ExecutionJobState.FAILED, "worker_lost")
    assert job.worker_active is False
    assert runner.calls == []
    assert _keys(env) == []


def test_cancelled_before_pickup_is_not_run(env):
    _enqueue(env)

    def _cancel(j):
        j.cancel_requested = True
        j.state = ExecutionJobState.CANCELLED

    env["store"].mutate("job_1", _cancel)
    runner = FakeNewmanRunner([])
    _worker(env, runner).process_one()
    assert runner.calls == []
    assert env["store"].get("job_1").state == ExecutionJobState.CANCELLED
    assert _keys(env) == []
