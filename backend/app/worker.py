"""Execution worker for the AWS backend (spec §5.2).

    python -m app.worker           # keep processing jobs
    python -m app.worker --once    # wait for one job, process it, then exit

In ECS each worker task processes one job and exits (`--once`), so every job
starts in a fresh task; the service's desired count scales with queue depth.
For each message: load the job and its encrypted material, drive the job
through the existing state machine with the Fargate task runner, store both
reports for the API, then delete the message and the material.

A redelivered message whose job was already started means a previous worker
died mid-run: that job is failed (`worker_lost`), never resumed half-way,
because run B must start from exactly the same inputs as run A.
"""

from __future__ import annotations

import argparse
import sys
import time

from .core import metrics
from .core.config import Settings, get_settings
from .core.logging import configure_logging, get_logger
from .core.production_guard import check_production_settings
from .domain.execution_job import ExecutionJob, ExecutionJobState, NewmanRunner
from .repositories.execution_job_store import ExecutionJobStore
from .repositories.s3_object_store import S3ObjectStore, job_prefix, material_key, report_key
from .services import execution_job_service
from .services.job_launcher import RunMaterial
from .services.job_queue import QueueMessage, SqsJobQueue

log = get_logger("worker")

_MAX_MATERIAL_BYTES = 64 * 1024 * 1024


class Worker:
    def __init__(
        self,
        *,
        settings: Settings,
        queue: SqsJobQueue,
        job_store: ExecutionJobStore,
        objects: S3ObjectStore,
        runner: NewmanRunner,
        receive_wait_seconds: int = 20,
    ) -> None:
        self._settings = settings
        self._queue = queue
        self._store = job_store
        self._objects = objects
        self._runner = runner
        self._wait = receive_wait_seconds

    def process_one(self) -> bool:
        """Handle at most one message; False when the queue was empty."""
        messages = self._queue.receive(
            wait_seconds=self._wait, visibility_timeout=self._settings.job_visibility_timeout_seconds,
        )
        if not messages:
            return False
        for message in messages:
            self._handle(message)
        return True

    def _discard(self, message: QueueMessage) -> None:
        self._queue.delete(message)
        self._objects.delete_prefix(job_prefix(message.job_id))

    def _handle(self, message: QueueMessage) -> None:
        job_id = message.job_id
        job = self._store.get(job_id)
        if job is None:
            self._discard(message)  # deleted or expired while queued
            return
        if job.state != ExecutionJobState.QUEUED:
            if job.is_active and not job.reports_ready:
                log.warning("job started by a lost worker", extra={"stage": "worker", "job_id": job_id})
                execution_job_service.fail_job(
                    job_id, self._store, "worker_lost", "The execution worker stopped unexpectedly. Run the job again.",
                )
                self._store.mutate(job_id, _release_worker)
            if not job.reports_ready:
                self._discard(message)
            else:
                self._queue.delete(message)  # the API still needs the reports
            return

        document = self._objects.get_json(material_key(job_id), max_bytes=_MAX_MATERIAL_BYTES)
        if document is None:
            execution_job_service.fail_job(
                job_id, self._store, "runner_unavailable", "The job's input is no longer available. Run it again.",
            )
            self._discard(message)
            return
        material = RunMaterial.from_document(document)
        metrics.emit("QueueWaitSeconds", round(max(0.0, time.time() - message.enqueued_at), 3), unit="Seconds")
        log.info("job picked up", extra={"stage": "worker", "job_id": job_id, "count": message.receive_count})
        execution_job_service.run_job(
            job_id,
            collection_data=material.collection_data,
            environment_data=material.environment_data,
            variable_values=material.variable_values,
            supplied_values=material.supplied_values,
            folder_id=material.folder_id,
            runner=self._runner,
            store=self._store,
            analysis_store=None,
            settings=self._settings,
            hand_off_reports=self._hand_off,
        )
        self._objects.delete(material_key(job_id))
        final = self._store.get(job_id)
        if final is None or not final.reports_ready:
            self._objects.delete_prefix(job_prefix(job_id))
        self._queue.delete(message)

    def _hand_off(self, job_id: str, baseline: bytes, comparison: bytes) -> None:
        self._objects.put_bytes(report_key(job_id, "baseline"), baseline)
        self._objects.put_bytes(report_key(job_id, "comparison"), comparison)

        def _ready(job: ExecutionJob) -> None:
            job.reports_ready = True

        self._store.mutate(job_id, _ready)


def _release_worker(job: ExecutionJob) -> None:
    job.worker_active = False


def build_worker(settings: Settings) -> Worker:
    from .api import deps
    from .core.aws import aws_client, require
    from .services.ecs_newman_runner import DockerTaskLauncher, EcsTaskLauncher, EcsTaskNewmanRunner

    require(settings, "aws_region", "jobs_table", "material_bucket", "job_queue_url")
    objects = deps.get_object_store()
    if settings.newman_runner == "ecs":
        require(settings, "ecs_cluster", "runner_task_definition", "runner_subnets")
        launcher: EcsTaskLauncher | DockerTaskLauncher = EcsTaskLauncher(
            client=aws_client("ecs", settings), settings=settings,
        )
    elif settings.newman_runner == "docker":
        launcher = DockerTaskLauncher(settings=settings)  # local end-to-end only
    else:
        raise RuntimeError("The worker needs B11_NEWMAN_RUNNER=ecs (or docker for local testing).")
    return Worker(
        settings=settings,
        queue=deps.get_job_queue(),
        job_store=deps.get_job_store(),
        objects=objects,
        runner=EcsTaskNewmanRunner(settings, objects=objects, launcher=launcher),
    )


def run_once(worker: Worker) -> None:
    """Wait (long-polling) until one job arrives, process it, and return. In
    ECS the task then exits, so every job gets a fresh worker task."""
    while not worker.process_one():
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Baseline11 execution worker")
    parser.add_argument("--once", action="store_true", help="process at most one job, then exit")
    args = parser.parse_args(argv)
    configure_logging()
    settings = get_settings()
    check_production_settings(settings, role="worker")
    worker = build_worker(settings)
    log.info("worker started", extra={"stage": "worker"})
    if args.once:
        run_once(worker)
        return 0
    while True:
        worker.process_one()


if __name__ == "__main__":
    sys.exit(main())
