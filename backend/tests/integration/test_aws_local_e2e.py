"""AWS backend end-to-end on this machine: the real API code, DynamoDB/S3/SQS
served by a moto server container, the real worker, and the real runner
image started per run (DockerTaskLauncher standing in for Fargate), running
the fixture collection against postman-echo.com.

Opt-in: B11_RUN_AWS_LOCAL_TESTS=1, Docker running, images built
(docker/newman-runner), and `docker compose -f docker/compose.aws-local.yml up -d`.
"""

import os
import urllib.request
from pathlib import Path

import boto3
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.aws_local

MOTO = "http://127.0.0.1:5000"
REGION = "eu-west-1"
_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "newman" / "echo_collection.json"


def _ready() -> bool:
    if os.environ.get("B11_RUN_AWS_LOCAL_TESTS") != "1":
        return False
    try:
        urllib.request.urlopen(f"{MOTO}/moto-api/", timeout=3)
    except OSError:
        return False
    return True


if not _ready():
    pytest.skip("AWS-local e2e is opt-in (B11_RUN_AWS_LOCAL_TESTS=1 and the moto compose stack)",
                allow_module_level=True)


@pytest.fixture
def aws_local(monkeypatch):
    from app.api import deps
    from app.core.config import get_settings

    for key, value in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                       "AWS_DEFAULT_REGION": REGION}.items():
        monkeypatch.setenv(key, value)
    urllib.request.urlopen(urllib.request.Request(f"{MOTO}/moto-api/reset", method="POST"), timeout=5)
    kw = dict(region_name=REGION, endpoint_url=MOTO)
    dynamo = boto3.client("dynamodb", **kw)
    dynamo.create_table(TableName="b11-jobs", AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
                        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}], BillingMode="PAY_PER_REQUEST")
    boto3.client("s3", **kw).create_bucket(Bucket="b11-material",
                                           CreateBucketConfiguration={"LocationConstraint": REGION})
    queue_url = boto3.client("sqs", **kw).create_queue(QueueName="b11-jobs")["QueueUrl"]
    for key, value in {
        "B11_JOB_BACKEND": "aws", "B11_AWS_REGION": REGION, "B11_AWS_ENDPOINT_URL": MOTO,
        "B11_JOBS_TABLE": "b11-jobs", "B11_MATERIAL_BUCKET": "b11-material", "B11_JOB_QUEUE_URL": queue_url,
        "B11_NEWMAN_RUNNER": "docker",
    }.items():
        monkeypatch.setenv(key, value)
    cached = (get_settings, deps.get_job_store, deps.get_object_store, deps.get_job_queue)
    for fn in cached:
        fn.cache_clear()
    yield
    for fn in cached:
        fn.cache_clear()


def test_collection_runs_through_queue_worker_and_fargate_stand_in(aws_local):
    from app.api import deps
    from app.core.config import get_settings
    from app.main import create_app
    from app.services.ecs_newman_runner import DockerTaskLauncher, EcsTaskNewmanRunner
    from app.worker import Worker

    settings = get_settings()
    client = TestClient(create_app())
    created = client.post(
        "/api/v1/execution-jobs", data={"confirm": "true", "supplied_values_json": "{}"},
        files={"collection": ("echo.json", _FIXTURE.read_bytes(), "application/json")},
    )
    assert created.status_code == 202, created.text
    job_id = created.json()["job_id"]

    objects = deps.get_object_store()
    launcher = DockerTaskLauncher(settings=settings, network="b11-aws-local",
                                  url_host_rewrite=(MOTO, "http://b11-moto:5000"))
    worker = Worker(settings=settings, queue=deps.get_job_queue(), job_store=deps.get_job_store(), objects=objects,
                    runner=EcsTaskNewmanRunner(settings, objects=objects, launcher=launcher, poll_interval=1),
                    receive_wait_seconds=1)
    assert worker.process_one() is True

    status = client.get(f"/api/v1/execution-jobs/{job_id}").json()
    assert status["state"] == "ready", status
    analysis = client.get(f"/api/v1/analyses/{status['analysis_id']}").json()
    assert analysis["mode"] == "two_run"
    assert analysis["candidate_count"] >= 1
    s3 = boto3.client("s3", region_name=REGION, endpoint_url=MOTO)
    assert s3.list_objects_v2(Bucket="b11-material").get("Contents") is None
