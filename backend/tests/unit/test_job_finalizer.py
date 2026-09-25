import json
import threading
import time

import boto3
import pytest

from app.core.config import Settings
from app.domain.execution_job import ExecutionJob, ExecutionJobState
from app.repositories.analysis_store import InMemorySessionStore
from app.repositories.execution_job_store import InMemoryExecutionJobStore
from app.repositories.s3_object_store import S3ObjectStore, report_key
from app.services.job_finalizer import finalize_job, needs_finalizing
from tests.fixtures import builders as b
from tests.fixtures.aws import BUCKET, REGION, aws_mocks


def _report(token: str) -> bytes:
    return json.dumps(b.report("Flow", [
        b.execution("Login", "POST", "https://api.example.com/login", resp_body={"token": token}, position=0),
        b.execution("Me", "GET", "https://api.example.com/me",
                    req_headers=[b.header("Authorization", f"Bearer {token}")], position=1),
    ])).encode()


@pytest.fixture
def env():
    with aws_mocks():
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
        yield {
            "s3": s3,
            "objects": S3ObjectStore(client=s3, bucket=BUCKET, kms_key_id=None),
            "store": InMemoryExecutionJobStore(ttl_seconds=600),
            "analyses": InMemorySessionStore(ttl_seconds=600),
        }


def _ready_for_analysis(env, baseline=None, comparison=None):
    now = time.time()
    job = ExecutionJob(id="job_1", owner_key="user:a", collection_name="Flow", created_at=now, expires_at=now + 600,
                       state=ExecutionJobState.ANALYZING, reports_ready=True,
                       stage_history=["validating", "running_baseline", "running_comparison", "analyzing"])
    env["store"].create(job)
    env["objects"].put_bytes(report_key("job_1", "baseline"), baseline or _report("tok_A1b2C3"))
    env["objects"].put_bytes(report_key("job_1", "comparison"), comparison or _report("tok_Z9y8X7"))
    return job


def _finalize(env):
    finalize_job("job_1", store=env["store"], objects=env["objects"], analysis_store=env["analyses"],
                 settings=Settings())


def test_only_analyzing_jobs_with_reports_need_finalizing(env):
    job = _ready_for_analysis(env)
    assert needs_finalizing(job) is True
    job.reports_ready = False
    assert needs_finalizing(job) is False


def test_builds_the_analysis_for_the_owner_and_cleans_up(env):
    _ready_for_analysis(env)
    _finalize(env)
    job = env["store"].get("job_1")
    assert job.state == ExecutionJobState.READY
    analysis = env["analyses"].get(job.analysis_id)
    assert analysis.owner_key == "user:a"
    assert analysis.mode.value == "two_run"
    assert env["s3"].list_objects_v2(Bucket=BUCKET).get("Contents") is None


def test_concurrent_finalizers_build_one_analysis(env):
    _ready_for_analysis(env)
    threads = [threading.Thread(target=_finalize, args=(env,)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(env["analyses"]._data) == 1
    assert env["store"].get("job_1").state == ExecutionJobState.READY


def test_unreadable_reports_fail_the_job(env):
    _ready_for_analysis(env, comparison=b"{broken")
    _finalize(env)
    job = env["store"].get("job_1")
    assert job.state == ExecutionJobState.FAILED
    assert job.error_code
    assert env["s3"].list_objects_v2(Bucket=BUCKET).get("Contents") is None


def test_missing_reports_fail_the_job(env):
    _ready_for_analysis(env)
    env["objects"].delete(report_key("job_1", "comparison"))
    _finalize(env)
    assert env["store"].get("job_1").error_code == "missing_report"
