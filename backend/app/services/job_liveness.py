"""Keeping long jobs alive, and noticing when their worker is gone.

* ``Heartbeat`` runs beside a job in the worker. Every ``interval`` seconds it
  extends the SQS message's visibility (so a healthy long run is never handed
  to a second worker) and records ``heartbeat_at`` on the job.
* ``TaskProtection`` asks the ECS agent to protect the worker task from
  scale-in and deployments while it holds a job, and releases it after.
  Outside ECS (no ``ECS_AGENT_URI``) it does nothing.
* ``reconcile_stale`` is the API-side check: a job whose worker stopped
  heartbeating, or that no worker picked up in time, ends FAILED with a clear
  message instead of showing "running" forever.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from typing import Any

from ..core.config import Settings
from ..core.logging import get_logger
from ..domain.execution_job import ExecutionJob, ExecutionJobState
from ..repositories.execution_job_store import ExecutionJobStore

log = get_logger("job_liveness")


class Heartbeat:
    def __init__(
        self,
        *,
        job_id: str,
        store: ExecutionJobStore,
        extend_visibility: Any,
        interval: float,
        visibility_timeout: int,
        clock: Any = time.time,
    ) -> None:
        self._job_id = job_id
        self._store = store
        self._extend = extend_visibility
        self._interval = max(interval, 0.05)
        self._visibility = visibility_timeout
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.beats = 0

    def beat(self) -> None:
        now = self._clock()

        def _stamp(job: ExecutionJob) -> None:
            job.heartbeat_at = now

        try:
            self._store.mutate(self._job_id, _stamp)
        except Exception:  # noqa: BLE001 - one missed beat is tolerated
            log.warning("heartbeat: job update failed", extra={"stage": "heartbeat", "job_id": self._job_id})
        try:
            self._extend(self._visibility)
        except Exception:  # noqa: BLE001
            log.warning("heartbeat: visibility extension failed", extra={"stage": "heartbeat", "job_id": self._job_id})
        self.beats += 1

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            self.beat()

    def __enter__(self) -> Heartbeat:
        self.beat()
        self._thread = threading.Thread(target=self._run, name=f"heartbeat-{self._job_id}", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)


class TaskProtection:
    """ECS task scale-in protection through the container agent endpoint."""

    def __init__(self, *, expires_in_minutes: int, agent_uri: str | None = None, opener: Any = None) -> None:
        self._uri = agent_uri if agent_uri is not None else os.environ.get("ECS_AGENT_URI")
        self._minutes = max(1, min(expires_in_minutes, 2880))
        self._open = opener or urllib.request.urlopen

    @property
    def available(self) -> bool:
        return bool(self._uri)

    def _set(self, enabled: bool) -> bool:
        if not self._uri:
            return False
        body: dict[str, Any] = {"ProtectionEnabled": enabled}
        if enabled:
            body["ExpiresInMinutes"] = self._minutes
        request = urllib.request.Request(
            f"{self._uri.rstrip('/')}/task-protection/v1/state",
            data=json.dumps(body).encode(), method="PUT", headers={"Content-Type": "application/json"},
        )
        try:
            with self._open(request, timeout=5) as response:  # noqa: S310 - ECS agent, link-local
                return 200 <= int(getattr(response, "status", 200)) < 300
        except Exception:  # noqa: BLE001 - protection is best effort
            log.warning("task protection update failed", extra={"stage": "protection"})
            return False

    def __enter__(self) -> TaskProtection:
        self._set(True)
        return self

    def __exit__(self, *exc: object) -> None:
        self._set(False)


_RUNNING = {
    ExecutionJobState.VALIDATING,
    ExecutionJobState.RUNNING_BASELINE,
    ExecutionJobState.RUNNING_COMPARISON,
}


def stale_reason(job: ExecutionJob, settings: Settings, *, now: float) -> tuple[str, str] | None:
    """Why this job can no longer finish, or None while it still can."""
    if not job.is_active or job.reports_ready:
        return None
    if job.state == ExecutionJobState.QUEUED:
        if now - job.created_at > settings.job_queue_timeout_seconds:
            return ("queue_timeout", "No execution worker became available in time. Run the job again.")
        return None
    if job.state in _RUNNING:
        last = job.heartbeat_at or job.created_at
        if now - last > settings.job_heartbeat_timeout_seconds:
            return ("worker_lost", "The execution worker stopped unexpectedly. Run the job again.")
    return None


def reconcile_stale(job: ExecutionJob, store: ExecutionJobStore, settings: Settings, *, now: float) -> bool:
    """Fail a job whose worker is gone. Returns whether it was failed."""
    from . import execution_job_service

    reason = stale_reason(job, settings, now=now)
    if reason is None:
        return False
    log.warning(f"stale job: {reason[0]}", extra={"stage": "reconcile", "job_id": job.id})
    written = execution_job_service.fail_job(job.id, store, *reason)

    def _release(j: ExecutionJob) -> None:
        j.worker_active = False

    store.mutate(job.id, _release)
    return written
