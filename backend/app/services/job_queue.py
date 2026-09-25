"""SQS queue between the API (enqueues) and the execution worker (receives).

A message carries only the job id and when it was enqueued - never job
material or any variable value; that lives encrypted in S3.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from ..core.logging import get_logger

log = get_logger("job_queue")


@dataclass(frozen=True)
class QueueMessage:
    job_id: str
    receipt_handle: str
    receive_count: int
    enqueued_at: float


class SqsJobQueue:
    def __init__(self, *, client: Any, queue_url: str) -> None:
        self._sqs = client
        self._url = queue_url

    def enqueue(self, job_id: str) -> None:
        body = json.dumps({"job_id": job_id, "enqueued_at": time.time()})
        self._sqs.send_message(QueueUrl=self._url, MessageBody=body)

    def receive(self, *, wait_seconds: int, visibility_timeout: int, max_messages: int = 1) -> list[QueueMessage]:
        resp = self._sqs.receive_message(
            QueueUrl=self._url,
            MaxNumberOfMessages=max_messages,
            WaitTimeSeconds=wait_seconds,
            VisibilityTimeout=visibility_timeout,
            MessageSystemAttributeNames=["ApproximateReceiveCount"],
        )
        messages: list[QueueMessage] = []
        for raw in resp.get("Messages", []):
            try:
                body = json.loads(raw["Body"])
                job_id = body["job_id"]
                if not isinstance(job_id, str) or not job_id:
                    raise ValueError("job_id")
                enqueued_at = float(body.get("enqueued_at", time.time()))
            except (ValueError, KeyError, TypeError):
                # Not ours / corrupt: drop it rather than redeliver it forever.
                log.warning("dropping malformed queue message", extra={"stage": "queue"})
                self._sqs.delete_message(QueueUrl=self._url, ReceiptHandle=raw["ReceiptHandle"])
                continue
            count = int(raw.get("Attributes", {}).get("ApproximateReceiveCount", "1"))
            messages.append(QueueMessage(job_id, raw["ReceiptHandle"], count, enqueued_at))
        return messages

    def delete(self, message: QueueMessage) -> None:
        self._sqs.delete_message(QueueUrl=self._url, ReceiptHandle=message.receipt_handle)

    def ping(self) -> None:
        """Readiness check: raises if the queue is unreachable."""
        self._sqs.get_queue_attributes(QueueUrl=self._url, AttributeNames=["QueueArn"])
