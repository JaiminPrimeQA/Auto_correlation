"""DynamoDB implementation of `ExecutionJobStore` for the AWS backend.

Single table, partition key `pk`:

- `JOB#{id}`: the job as a JSON `doc`, an optimistic-locking `version`, and a
  DynamoDB `ttl` (expiry plus a grace period, so TTL deletion never races a
  live worker).
- `IDEM#{owner}#{key}`: the most recent job created with that idempotency
  key, with `ttl` = the idempotency window.
- `SLOTS#{owner}`: `active` = how many of the owner's jobs count against the
  concurrency cap (active, or terminal with a live worker).

`create_if_allowed` inserts the job, claims the idempotency key and takes a
slot in ONE transaction, so concurrent submissions from several API tasks can
never exceed the cap or both win the same key. Every later write adjusts the
slot counter in the same transaction as the job write, exactly when the job
starts or stops counting. Owner and key are percent-encoded so a `#` in
either can never forge another item's key.
"""

from __future__ import annotations

import dataclasses
import json
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from botocore.exceptions import ClientError

from ..domain.execution_job import ExecutionJob, ExecutionJobState
from .execution_job_store import ExecutionJobStore, _is_evictable, too_many_active_jobs

_MAX_MUTATE_ATTEMPTS = 20
# Items outlive their logical expiry by a day so DynamoDB TTL deletion never
# removes a job a worker could still be driving.
_TTL_GRACE_SECONDS = 24 * 3600


def _job_pk(job_id: str) -> str:
    return f"JOB#{job_id}"


def _idem_pk(owner_key: str, idempotency_key: str) -> str:
    return f"IDEM#{quote(owner_key, safe='')}#{quote(idempotency_key, safe='')}"


def _slots_pk(owner_key: str) -> str:
    return f"SLOTS#{quote(owner_key, safe='')}"


def _counts(job: ExecutionJob) -> bool:
    return job.is_active or job.worker_active


def _to_doc(job: ExecutionJob) -> str:
    data = dataclasses.asdict(job)
    data["state"] = job.state.value
    return json.dumps(data, separators=(",", ":"))


def _from_doc(doc: str) -> ExecutionJob:
    data = json.loads(doc)
    data["state"] = ExecutionJobState(data["state"])
    known = {f.name for f in dataclasses.fields(ExecutionJob)}
    return ExecutionJob(**{k: v for k, v in data.items() if k in known})


def _is_conditional_failure(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") in (
        "ConditionalCheckFailedException", "TransactionCanceledException",
    )


def _cancellation_codes(exc: ClientError) -> list[str]:
    reasons = exc.response.get("CancellationReasons") or []
    return [str(r.get("Code", "None")) for r in reasons]


class DynamoDbExecutionJobStore(ExecutionJobStore):
    def __init__(self, *, client: Any, table_name: str, ttl_seconds: int) -> None:
        self._db = client
        self._table = table_name
        self.ttl = ttl_seconds

    # --- item helpers ---

    def _job_item(self, job: ExecutionJob, version: int) -> dict:
        return {
            "pk": {"S": _job_pk(job.id)},
            "doc": {"S": _to_doc(job)},
            "version": {"N": str(version)},
            "owner_key": {"S": job.owner_key},
            "ttl": {"N": str(int(job.expires_at) + _TTL_GRACE_SECONDS)},
        }

    def _slot_update(self, owner_key: str, delta: int, *, max_active: int | None = None) -> dict:
        update: dict[str, Any] = {
            "TableName": self._table,
            "Key": {"pk": {"S": _slots_pk(owner_key)}},
            "UpdateExpression": "SET active = if_not_exists(active, :zero) + :delta",
            "ExpressionAttributeValues": {":zero": {"N": "0"}, ":delta": {"N": str(delta)}},
        }
        if max_active is not None:
            update["ConditionExpression"] = "attribute_not_exists(active) OR active < :max"
            update["ExpressionAttributeValues"][":max"] = {"N": str(max_active)}
        return {"Update": update}

    def _read(self, job_id: str) -> tuple[ExecutionJob, int] | None:
        resp = self._db.get_item(TableName=self._table, Key={"pk": {"S": _job_pk(job_id)}}, ConsistentRead=True)
        item = resp.get("Item")
        if item is None:
            return None
        return _from_doc(item["doc"]["S"]), int(item["version"]["N"])

    def _write(self, job: ExecutionJob, *, previous: tuple[ExecutionJob, int] | None) -> None:
        """Put `job`, conditional on the version read, adjusting the owner's
        slot counter in the same transaction when counting changes."""
        items: list[dict] = []
        if previous is None:
            put = {"TableName": self._table, "Item": self._job_item(job, 1)}
            delta = 1 if _counts(job) else 0
        else:
            old, version = previous
            put = {
                "TableName": self._table,
                "Item": self._job_item(job, version + 1),
                "ConditionExpression": "version = :v",
                "ExpressionAttributeValues": {":v": {"N": str(version)}},
            }
            delta = int(_counts(job)) - int(_counts(old))
        items.append({"Put": put})
        if delta:
            items.append(self._slot_update(job.owner_key, delta))
        self._db.transact_write_items(TransactItems=items)

    def _remember_idempotency(self, job: ExecutionJob, window_seconds: int) -> dict:
        assert job.idempotency_key is not None
        return {
            "TableName": self._table,
            "Item": {
                "pk": {"S": _idem_pk(job.owner_key, job.idempotency_key)},
                "job_id": {"S": job.id},
                "created_at": {"N": repr(job.created_at)},
                "ttl": {"N": str(int(job.created_at + window_seconds) + 1)},
            },
        }

    def _evict(self, job: ExecutionJob, version: int) -> None:
        items: list[dict] = [{
            "Delete": {
                "TableName": self._table,
                "Key": {"pk": {"S": _job_pk(job.id)}},
                "ConditionExpression": "version = :v",
                "ExpressionAttributeValues": {":v": {"N": str(version)}},
            },
        }]
        if _counts(job):
            items.append(self._slot_update(job.owner_key, -1))
        try:
            self._db.transact_write_items(TransactItems=items)
        except ClientError as exc:
            if not _is_conditional_failure(exc):
                raise

    # --- ExecutionJobStore ---

    def create(self, job: ExecutionJob) -> None:
        existing = self._read(job.id)
        self._write(job, previous=existing)
        if job.idempotency_key:
            put = self._remember_idempotency(job, window_seconds=self.ttl)
            put["ConditionExpression"] = "attribute_not_exists(pk) OR created_at < :c"
            put["ExpressionAttributeValues"] = {":c": {"N": repr(job.created_at)}}
            try:
                self._db.put_item(**put)
            except ClientError as exc:
                if not _is_conditional_failure(exc):
                    raise  # an older job keeps a newer record: most recent wins

    def get(self, job_id: str) -> ExecutionJob | None:
        found = self._read(job_id)
        if found is None:
            return None
        job, version = found
        if _is_evictable(job, time.time()):
            self._evict(job, version)
            return None
        return job

    def update(self, job: ExecutionJob) -> None:
        for _ in range(_MAX_MUTATE_ATTEMPTS):
            try:
                self._write(job, previous=self._read(job.id))
                return
            except ClientError as exc:
                if not _is_conditional_failure(exc):
                    raise
        raise RuntimeError("Could not update the job: too much contention.")

    def delete(self, job_id: str) -> bool:
        found = self._read(job_id)
        if found is None:
            return False
        self._evict(*found)
        return True

    def find_by_idempotency_key(
        self, owner_key: str, idempotency_key: str, *, window_seconds: int
    ) -> ExecutionJob | None:
        resp = self._db.get_item(
            TableName=self._table, Key={"pk": {"S": _idem_pk(owner_key, idempotency_key)}}, ConsistentRead=True,
        )
        item = resp.get("Item")
        if item is None or float(item["created_at"]["N"]) < time.time() - window_seconds:
            return None
        return self.get(item["job_id"]["S"])

    def count_active(self, owner_key: str) -> int:
        resp = self._db.get_item(TableName=self._table, Key={"pk": {"S": _slots_pk(owner_key)}}, ConsistentRead=True)
        item = resp.get("Item")
        return max(0, int(item["active"]["N"])) if item else 0

    def create_if_allowed(
        self, job: ExecutionJob, *, window_seconds: int, max_active: int
    ) -> tuple[ExecutionJob, bool]:
        if job.idempotency_key:
            existing = self.find_by_idempotency_key(job.owner_key, job.idempotency_key, window_seconds=window_seconds)
            if existing is not None:
                return existing, False
        items: list[dict] = [
            {"Put": {
                "TableName": self._table,
                "Item": self._job_item(job, 1),
                "ConditionExpression": "attribute_not_exists(pk)",
            }},
            self._slot_update(job.owner_key, 1, max_active=max_active),
        ]
        if job.idempotency_key:
            idem = self._remember_idempotency(job, window_seconds)
            idem["ConditionExpression"] = "attribute_not_exists(pk) OR created_at < :window_start"
            idem["ExpressionAttributeValues"] = {":window_start": {"N": repr(time.time() - window_seconds)}}
            items.append({"Put": idem})
        try:
            self._db.transact_write_items(TransactItems=items)
        except ClientError as exc:
            if not _is_conditional_failure(exc):
                raise
            codes = _cancellation_codes(exc)
            if job.idempotency_key and len(codes) > 2 and codes[2] == "ConditionalCheckFailed":
                # Another request claimed the key first: it wins.
                existing = self.find_by_idempotency_key(
                    job.owner_key, job.idempotency_key, window_seconds=window_seconds,
                )
                if existing is not None:
                    return existing, False
            raise too_many_active_jobs(max_active) from exc
        return job, True

    def mutate(self, job_id: str, fn: Callable[[ExecutionJob], None]) -> ExecutionJob | None:
        for _ in range(_MAX_MUTATE_ATTEMPTS):
            found = self._read(job_id)
            if found is None:
                return None
            job, version = found
            if _is_evictable(job, time.time()):
                self._evict(job, version)
                return None
            before = _from_doc(_to_doc(job))
            fn(job)
            try:
                self._write(job, previous=(before, version))
                return job
            except ClientError as exc:
                if not _is_conditional_failure(exc):
                    raise
                time.sleep(0.005)
        raise RuntimeError("Could not update the job: too much contention.")
