"""Ephemeral execution-job state with TTL, mirroring `analysis_store.py`'s
SessionStore pattern exactly. Adds an owner+idempotency-key index and an
active-job counter that `create_job` uses to enforce concurrency and
idempotent resubmission - atomically, via `create_if_allowed`.
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable

from ..core.errors import ProblemException, rate_limited
from ..domain.execution_job import ExecutionJob


def too_many_active_jobs(max_active: int) -> ProblemException:
    """The 429 problem raised when an owner is at the active-job cap."""
    return rate_limited(f"Too many active execution jobs ({max_active} allowed at a time).")


def _is_evictable(job: ExecutionJob, now: float) -> bool:
    """Past its TTL and not being driven by a live worker - a running job must
    never silently vanish mid-run; it becomes evictable once the worker clears
    `worker_active`.
    """
    return job.expires_at < now and not job.worker_active


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

    @abstractmethod
    def create_if_allowed(
        self, job: ExecutionJob, *, window_seconds: int, max_active: int
    ) -> tuple[ExecutionJob, bool]:
        """Atomically insert `job` unless an idempotency hit or the active cap
        forbids it - the check and the insert happen under one lock
        acquisition, so concurrent submissions cannot all pass the check.

        Returns `(existing, False)` when `job.idempotency_key` matches a job
        from the same owner within `window_seconds` (the most recent one);
        raises the 429 `rate_limited` problem when the owner already has
        `max_active` active jobs; otherwise inserts and returns `(job, True)`.
        """

    @abstractmethod
    def mutate(self, job_id: str, fn: Callable[[ExecutionJob], None]) -> ExecutionJob | None:
        """Atomically look up `job_id` and, if it exists and is unexpired, apply
        `fn` to it and persist the result - all under one lock acquisition, so
        a concurrent delete/update cannot land between the check and the write.

        Returns the (mutated) job, or None if it is missing or expired -
        without inserting anything in that case.
        """


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
            if _is_evictable(job, time.time()):
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
            return self._find_by_idempotency_key_locked(owner_key, idempotency_key, window_seconds)

    def count_active(self, owner_key: str) -> int:
        with self._lock:
            self._cleanup()
            return self._count_active_locked(owner_key)

    def create_if_allowed(
        self, job: ExecutionJob, *, window_seconds: int, max_active: int
    ) -> tuple[ExecutionJob, bool]:
        with self._lock:
            self._cleanup()
            if job.idempotency_key:
                existing = self._find_by_idempotency_key_locked(job.owner_key, job.idempotency_key, window_seconds)
                if existing is not None:
                    return existing, False
            if self._count_active_locked(job.owner_key) >= max_active:
                raise too_many_active_jobs(max_active)
            self._data[job.id] = job
            return job, True

    def mutate(self, job_id: str, fn: Callable[[ExecutionJob], None]) -> ExecutionJob | None:
        with self._lock:
            self._cleanup()
            job = self._data.get(job_id)
            if job is None:
                return None
            if _is_evictable(job, time.time()):
                self._data.pop(job_id, None)
                return None
            fn(job)
            self._data[job.id] = job
            return job

    # --- helpers: caller must hold self._lock ---

    def _find_by_idempotency_key_locked(
        self, owner_key: str, idempotency_key: str, window_seconds: int
    ) -> ExecutionJob | None:
        now = time.time()
        cutoff = now - window_seconds
        candidates = [
            job
            for job in self._data.values()
            if job.owner_key == owner_key
            and job.idempotency_key == idempotency_key
            and not _is_evictable(job, now)
            and job.created_at >= cutoff
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda j: j.created_at)

    def _count_active_locked(self, owner_key: str) -> int:
        # A job whose worker is still live counts even if already terminal
        # (e.g. cancelled mid-run), so cancel-and-resubmit cannot exceed the cap.
        return sum(
            1 for j in self._data.values() if j.owner_key == owner_key and (j.is_active or j.worker_active)
        )

    def _cleanup(self) -> None:
        now = time.time()
        expired = [jid for jid, j in self._data.items() if _is_evictable(j, now)]
        for jid in expired:
            self._data.pop(jid, None)
