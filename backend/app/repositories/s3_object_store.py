"""Private, KMS-encrypted S3 storage for execution-job material and reports.

Layout (everything for one job lives under `jobs/{job_id}/` and is deleted
together; the bucket's lifecycle rule removes anything left after a day):

    jobs/{id}/material.json              collection, environment, supplied values
    jobs/{id}/runs/{stage}/input.json    one runner task's input (presigned GET)
    jobs/{id}/runs/{stage}/report.json   that task's Newman report (presigned PUT)
    jobs/{id}/reports/{stage}.json       reports handed from the worker to the API

Presigned URLs are the ONLY way a runner task touches S3 - it has no AWS
credentials. Uploads through them are encrypted by the bucket's default
SSE-KMS configuration.
"""

from __future__ import annotations

import json
import re
from typing import Any

from botocore.exceptions import ClientError

_JOB_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_STAGE = re.compile(r"^[a-z0-9_]{1,32}$")


class ObjectTooLarge(Exception):
    """The object exceeds the caller's size limit; it was not downloaded."""


def job_prefix(job_id: str) -> str:
    if not _JOB_ID.match(job_id):
        raise ValueError("Invalid job id for an object key.")
    return f"jobs/{job_id}/"


def _stage(stage: str) -> str:
    if not _STAGE.match(stage):
        raise ValueError("Invalid stage for an object key.")
    return stage


def material_key(job_id: str) -> str:
    return f"{job_prefix(job_id)}material.json"


def run_input_key(job_id: str, stage: str) -> str:
    return f"{job_prefix(job_id)}runs/{_stage(stage)}/input.json"


def run_report_key(job_id: str, stage: str) -> str:
    return f"{job_prefix(job_id)}runs/{_stage(stage)}/report.json"


def report_key(job_id: str, stage: str) -> str:
    return f"{job_prefix(job_id)}reports/{_stage(stage)}.json"


def _is_missing(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound")


class S3ObjectStore:
    def __init__(self, *, client: Any, bucket: str, kms_key_id: str | None) -> None:
        self._s3 = client
        self._bucket = bucket
        self._kms_key_id = kms_key_id

    def put_bytes(self, key: str, data: bytes, *, content_type: str = "application/json") -> None:
        extra: dict[str, str] = {"ServerSideEncryption": "aws:kms"}
        if self._kms_key_id:
            extra["SSEKMSKeyId"] = self._kms_key_id
        self._s3.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type, **extra)

    def put_json(self, key: str, document: Any) -> None:
        self.put_bytes(key, json.dumps(document, separators=(",", ":")).encode("utf-8"))

    def get_bytes(self, key: str, *, max_bytes: int) -> bytes | None:
        try:
            head = self._s3.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if _is_missing(exc):
                return None
            raise
        if int(head["ContentLength"]) > max_bytes:
            raise ObjectTooLarge(key)
        body = self._s3.get_object(Bucket=self._bucket, Key=key)["Body"]
        data: bytes = body.read(max_bytes + 1)
        if len(data) > max_bytes:  # grew between HEAD and GET
            raise ObjectTooLarge(key)
        return data

    def get_json(self, key: str, *, max_bytes: int) -> Any | None:
        data = self.get_bytes(key, max_bytes=max_bytes)
        return None if data is None else json.loads(data)

    def delete(self, key: str) -> None:
        self._s3.delete_object(Bucket=self._bucket, Key=key)

    def delete_prefix(self, prefix: str) -> None:
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if keys:
                self._s3.delete_objects(Bucket=self._bucket, Delete={"Objects": keys, "Quiet": True})

    def presigned_get(self, key: str, *, expires_seconds: int) -> str:
        url: str = self._s3.generate_presigned_url(
            "get_object", Params={"Bucket": self._bucket, "Key": key}, ExpiresIn=expires_seconds,
        )
        return url

    def presigned_put(self, key: str, *, expires_seconds: int) -> str:
        url: str = self._s3.generate_presigned_url(
            "put_object", Params={"Bucket": self._bucket, "Key": key}, ExpiresIn=expires_seconds,
        )
        return url
