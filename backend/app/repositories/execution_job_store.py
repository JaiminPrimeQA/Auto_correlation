"""Ephemeral execution-job state with TTL, mirroring `analysis_store.py`'s
SessionStore pattern exactly. Adds an owner+idempotency-key index and an
active-job counter that Task 5's `create_job` uses to enforce concurrency
and idempotent resubmission.
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod

from ..domain.execution_job import ExecutionJob


class ExecutionJobStore(ABC):
    @abstractmethod
    def create(self, job: ExecutionJob) -> None: ...

    @abstractmethod
    def get(self, job_id: str) -> ExecutionJob | None: ...

    @abstractmethod
    def update(self, job: ExecutionJob) -> None: ...

    @abstractmethod
    def delete(self, job_id: str) -> bool: ...

    @abstractmethod
    def find_by_idempotency_key(
        self, owner_key: str, idempotency_key: str, *, window_seconds: int
    ) -> ExecutionJob | None: ...

    @abstractmethod
    def count_active(self, owner_key: str) -> int: ...


class InMemoryExecutionJobStore(ExecutionJobStore):
    def __init__(self, ttl_seconds: int) -> None:
        self.ttl = ttl_seconds
        self._data: dict[str, ExecutionJob] = {}
        self._lock = threading.RLock()

    def create(self, job: ExecutionJob) -> None:
        with self._lock:
            self._data[job.id] = job

    def get(self, job_id: str) -> ExecutionJob | None:
        with self._lock:
            self._cleanup()
            job = self._data.get(job_id)
            if job is None:
                return None
            if job.expires_at < time.time():
                self._data.pop(job_id, None)
                return None
            return job

    def update(self, job: ExecutionJob) -> None:
        with self._lock:
            self._data[job.id] = job

    def delete(self, job_id: str) -> bool:
        with self._lock:
            return self._data.pop(job_id, None) is not None

    def find_by_idempotency_key(
        self, owner_key: str, idempotency_key: str, *, window_seconds: int
    ) -> ExecutionJob | None:
        with self._lock:
            self._cleanup()
            now = time.time()
            cutoff = now - window_seconds
            candidates = [
                job
                for job in self._data.values()
                if job.owner_key == owner_key
                and job.idempotency_key == idempotency_key
                and job.expires_at >= now
                and job.created_at >= cutoff
            ]
            if not candidates:
                return None
            return max(candidates, key=lambda j: j.created_at)

    def count_active(self, owner_key: str) -> int:
        with self._lock:
            self._cleanup()
            return sum(1 for j in self._data.values() if j.owner_key == owner_key and j.is_active)

    def _cleanup(self) -> None:
        now = time.time()
        expired = [jid for jid, j in self._data.items() if j.expires_at < now]
        for jid in expired:
            self._data.pop(jid, None)
