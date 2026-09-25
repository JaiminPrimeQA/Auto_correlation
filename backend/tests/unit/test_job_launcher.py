import json
import time

import boto3
import pytest

from app.domain.execution_job import ExecutionJob, ExecutionJobState
from app.repositories.execution_job_store import InMemoryExecutionJobStore
from app.repositories.s3_object_store import S3ObjectStore, material_key
from app.services.job_launcher import QueueJobLauncher, RunMaterial
from app.services.job_queue import SqsJobQueue
from tests.fixtures.aws import BUCKET, REGION, aws_mocks

_MATERIAL = RunMaterial(
    collection_data={"info": {"name": "C"}, "item": []},
    environment_data=None,
    variable_values={"host": "api.example.com", "token": "s3cret-token"},
    supplied_values={"token": "s3cret-token"},
    folder_id=None,
)


@pytest.fixture
def aws():
    with aws_mocks():
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
        sqs = boto3.client("sqs", region_name=REGION)
        queue_url = sqs.create_queue(QueueName="b11-jobs")["QueueUrl"]
        yield s3, sqs, queue_url


def _queued_job(store, job_id="job_1"):
    now = time.time()
    job = ExecutionJob(id=job_id, owner_key="user:a", collection_name="C", created_at=now, expires_at=now + 60)
    store.create(job)
    return job


def test_material_document_round_trips():
    assert RunMaterial.from_document(_MATERIAL.to_document()) == _MATERIAL


def test_launch_stores_encrypted_material_and_enqueues_only_the_job_id(aws):
    s3, sqs, queue_url = aws
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    objects = S3ObjectStore(client=s3, bucket=BUCKET, kms_key_id=None)
    QueueJobLauncher(objects=objects, queue=SqsJobQueue(client=sqs, queue_url=queue_url), job_store=store).launch(
        job.id, _MATERIAL,
    )
    stored = objects.get_json(material_key(job.id), max_bytes=10_000)
    assert RunMaterial.from_document(stored) == _MATERIAL
    assert s3.head_object(Bucket=BUCKET, Key=material_key(job.id))["ServerSideEncryption"] == "aws:kms"
    messages = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10)["Messages"]
    assert len(messages) == 1
    assert json.loads(messages[0]["Body"])["job_id"] == job.id
    assert "s3cret" not in messages[0]["Body"]
    assert store.get(job.id).state == ExecutionJobState.QUEUED


def test_enqueue_failure_fails_the_job_and_removes_its_material(aws):
    s3, _, _ = aws

    class _BrokenQueue:
        def enqueue(self, job_id):
            raise RuntimeError("sqs down")

    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    objects = S3ObjectStore(client=s3, bucket=BUCKET, kms_key_id=None)
    QueueJobLauncher(objects=objects, queue=_BrokenQueue(), job_store=store).launch(job.id, _MATERIAL)
    failed = store.get(job.id)
    assert failed.state == ExecutionJobState.FAILED
    assert failed.error_code == "runner_unavailable"
    assert "sqs down" not in (failed.error_detail or "")
    assert objects.get_json(material_key(job.id), max_bytes=10_000) is None


def test_queue_receive_reports_attempts_and_can_delete(aws):
    _, sqs, queue_url = aws
    queue = SqsJobQueue(client=sqs, queue_url=queue_url)
    queue.enqueue("job_9")
    [message] = queue.receive(wait_seconds=0, visibility_timeout=30)
    assert (message.job_id, message.receive_count) == ("job_9", 1)
    assert message.enqueued_at <= time.time()
    queue.delete(message)
    assert queue.receive(wait_seconds=0, visibility_timeout=30) == []


def test_queue_skips_and_deletes_malformed_messages(aws):
    _, sqs, queue_url = aws
    sqs.send_message(QueueUrl=queue_url, MessageBody="not json")
    queue = SqsJobQueue(client=sqs, queue_url=queue_url)
    assert queue.receive(wait_seconds=0, visibility_timeout=30) == []
    assert "Messages" not in sqs.receive_message(QueueUrl=queue_url, WaitTimeSeconds=0)
