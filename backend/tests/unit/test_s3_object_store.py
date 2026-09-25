import boto3
import pytest
import requests

from app.repositories.s3_object_store import (
    ObjectTooLarge,
    S3ObjectStore,
    job_prefix,
    material_key,
    report_key,
    run_input_key,
    run_report_key,
)
from tests.fixtures.aws import BUCKET, REGION, aws_mocks


@pytest.fixture
def s3():
    with aws_mocks():
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
        yield client, S3ObjectStore(client=client, bucket=BUCKET, kms_key_id="alias/b11-test")


def test_key_layout_is_scoped_by_job():
    assert material_key("j1") == "jobs/j1/material.json"
    assert run_input_key("j1", "baseline") == "jobs/j1/runs/baseline/input.json"
    assert run_report_key("j1", "baseline") == "jobs/j1/runs/baseline/report.json"
    assert report_key("j1", "comparison") == "jobs/j1/reports/comparison.json"
    assert job_prefix("j1") == "jobs/j1/"
    with pytest.raises(ValueError):
        material_key("../other")


def test_json_round_trip_is_kms_encrypted(s3):
    client, store = s3
    store.put_json("jobs/j1/material.json", {"supplied": {"token": "s3cret"}})
    assert store.get_json("jobs/j1/material.json", max_bytes=1024) == {"supplied": {"token": "s3cret"}}
    head = client.head_object(Bucket=BUCKET, Key="jobs/j1/material.json")
    assert head["ServerSideEncryption"] == "aws:kms"


def test_missing_object_is_none(s3):
    _, store = s3
    assert store.get_json("jobs/j1/nope.json", max_bytes=10) is None
    assert store.get_bytes("jobs/j1/nope.json", max_bytes=10) is None


def test_oversized_object_is_rejected_before_download(s3):
    _, store = s3
    store.put_bytes("jobs/j1/runs/baseline/report.json", b"x" * 100)
    with pytest.raises(ObjectTooLarge):
        store.get_bytes("jobs/j1/runs/baseline/report.json", max_bytes=10)


def test_delete_prefix_removes_only_that_job(s3):
    client, store = s3
    for key in ("jobs/j1/material.json", "jobs/j1/reports/baseline.json", "jobs/j10/material.json"):
        store.put_bytes(key, b"{}")
    store.delete_prefix(job_prefix("j1"))
    remaining = [o["Key"] for o in client.list_objects_v2(Bucket=BUCKET).get("Contents", [])]
    assert remaining == ["jobs/j10/material.json"]


def test_presigned_urls_grant_get_and_put_on_one_object(s3):
    _, store = s3
    store.put_json("jobs/j1/runs/baseline/input.json", {"collection": {}})
    get_url = store.presigned_get("jobs/j1/runs/baseline/input.json", expires_seconds=60)
    put_url = store.presigned_put("jobs/j1/runs/baseline/report.json", expires_seconds=60)
    assert "jobs/j1/runs/baseline/input.json" in get_url
    assert "Signature" in get_url
    # moto intercepts `requests` calls in-process; nothing reaches AWS.
    got = requests.get(get_url, timeout=5)
    assert got.status_code == 200
    assert got.json() == {"collection": {}}
    put = requests.put(put_url, data=b'{"run": {"executions": []}}', timeout=5)
    assert put.status_code == 200
    assert store.get_bytes("jobs/j1/runs/baseline/report.json", max_bytes=1024) == b'{"run": {"executions": []}}'
