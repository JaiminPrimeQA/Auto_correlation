"""POST /execution-jobs with B11_JOB_BACKEND=aws (moto): the API stores the
job in DynamoDB, the material in S3 and enqueues the id - it never runs the
collection itself."""

import json

import boto3
import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.core.config import get_settings
from app.main import create_app
from tests.fixtures.aws import BUCKET, REGION, TABLE, aws_mocks, create_jobs_table
from tests.fixtures.postman_builders import pm_collection, pm_request

_CACHED = (get_settings, deps.get_job_store, deps.get_object_store, deps.get_job_queue)


@pytest.fixture
def aws_app(monkeypatch):
    with aws_mocks():
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
        sqs = boto3.client("sqs", region_name=REGION)
        queue_url = sqs.create_queue(QueueName="b11-jobs")["QueueUrl"]
        create_jobs_table(boto3.client("dynamodb", region_name=REGION))
        for key, value in {
            "B11_JOB_BACKEND": "aws", "B11_AWS_REGION": REGION, "B11_JOBS_TABLE": TABLE,
            "B11_MATERIAL_BUCKET": BUCKET, "B11_JOB_QUEUE_URL": queue_url,
        }.items():
            monkeypatch.setenv(key, value)
        for fn in _CACHED:
            fn.cache_clear()
        try:
            yield TestClient(create_app()), s3, sqs, queue_url
        finally:
            for fn in _CACHED:
                fn.cache_clear()


def _post(client, supplied=None, key="k1"):
    collection = pm_collection("Smoke", [pm_request("Ping", "GET", "https://{{host}}/ping")])
    return client.post(
        "/api/v1/execution-jobs",
        data={"confirm": "true", "supplied_values_json": json.dumps(supplied or {"host": "93.184.216.34"})},
        files={"collection": ("c.json", json.dumps(collection).encode(), "application/json")},
        headers={"Idempotency-Key": key},
    )


def test_create_queues_the_job_without_running_it(aws_app):
    client, s3, sqs, queue_url = aws_app
    resp = _post(client)
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    assert resp.json()["state"] == "queued"
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET)["Contents"]]
    assert keys == [f"jobs/{job_id}/material.json"]
    [message] = sqs.receive_message(QueueUrl=queue_url)["Messages"]
    assert json.loads(message["Body"])["job_id"] == job_id
    assert client.get(f"/api/v1/execution-jobs/{job_id}").json()["state"] == "queued"


def test_idempotent_repeat_enqueues_once(aws_app):
    client, _, sqs, queue_url = aws_app
    first, second = _post(client, key="same"), _post(client, key="same")
    assert first.json()["job_id"] == second.json()["job_id"]
    messages = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10).get("Messages", [])
    assert len(messages) == 1


def test_full_round_trip_api_worker_api_reaches_ready(aws_app):
    from app.services.fake_newman_runner import canned_fake_runner
    from app.worker import Worker

    client, s3, *_ = aws_app
    job_id = _post(client).json()["job_id"]
    worker = Worker(settings=get_settings(), queue=deps.get_job_queue(), job_store=deps.get_job_store(),
                    objects=deps.get_object_store(), runner=canned_fake_runner(), receive_wait_seconds=0)
    assert worker.process_one() is True

    status = client.get(f"/api/v1/execution-jobs/{job_id}").json()
    assert status["state"] == "ready", status
    assert status["stage_history"] == ["validating", "running_baseline", "running_comparison", "analyzing", "ready"]
    assert client.get(f"/api/v1/analyses/{status['analysis_id']}").status_code == 200
    assert s3.list_objects_v2(Bucket=BUCKET).get("Contents") is None  # material and reports removed


def test_deleting_a_queued_job_removes_its_material(aws_app):
    client, s3, *_ = aws_app
    job_id = _post(client).json()["job_id"]
    assert client.delete(f"/api/v1/execution-jobs/{job_id}").status_code == 204  # cancels
    assert client.delete(f"/api/v1/execution-jobs/{job_id}").status_code == 204  # removes
    assert s3.list_objects_v2(Bucket=BUCKET).get("Contents") is None


def test_create_works_without_a_local_newman_runner(aws_app):
    client, *_ = aws_app
    # newman_runner stays "disabled" in the API: execution belongs to the worker.
    assert get_settings().newman_runner == "disabled"
    assert _post(client).status_code == 202
