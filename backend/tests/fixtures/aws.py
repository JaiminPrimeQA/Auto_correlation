"""moto-backed AWS resources for unit tests. Nothing here reaches real AWS."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

import boto3
from moto import mock_aws

REGION = "eu-west-1"
TABLE = "b11-jobs-test"
BUCKET = "b11-material-test"


def _fake_credentials() -> None:
    for key, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": REGION,
    }.items():
        os.environ[key] = value


def create_jobs_table(client) -> None:
    """The same key schema infra/terraform/dynamodb.tf declares."""
    client.create_table(
        TableName=TABLE,
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
        BillingMode="PAY_PER_REQUEST",
    )


@contextmanager
def aws_mocks() -> Iterator[None]:
    _fake_credentials()
    with mock_aws():
        yield


@contextmanager
def dynamodb_job_store(ttl_seconds: int = 60) -> Iterator:
    from app.repositories.dynamodb_job_store import DynamoDbExecutionJobStore

    with aws_mocks():
        client = boto3.client("dynamodb", region_name=REGION)
        create_jobs_table(client)
        yield DynamoDbExecutionJobStore(client=client, table_name=TABLE, ttl_seconds=ttl_seconds)
