"""Run one Newman execution as a short-lived Fargate task (spec §5.2).

The worker writes the run's input (collection, environment with the supplied
values applied, and the Newman arguments) to S3, then starts a runner task
whose ONLY capabilities are two presigned URLs: GET that input, PUT the
report. The task definition has no task role, so a hostile collection script
cannot reach AWS credentials; the runner subnet's network ACL blocks private,
carrier-grade NAT and link-local ranges (DNS rebinding cannot reach them).

The worker polls the task, stops it on cancellation or when the wall-clock
deadline passes, maps how it stopped to the same stable error codes as the
Docker runner, and always deletes the run's objects.

`TaskLauncher` has two implementations: `EcsTaskLauncher` (RunTask in AWS)
and `DockerTaskLauncher` (the same runner image under local Docker, for the
local AWS end-to-end test).
"""

from __future__ import annotations

import os
import secrets
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from ..core.config import Settings
from ..core.logging import get_logger
from ..domain.execution_job import NewmanRunner, RunInput, RunOutcome
from ..repositories.s3_object_store import ObjectTooLarge, S3ObjectStore, run_input_key, run_report_key
from .newman_command import newman_run_args
from .newman_report_adapter import validate_report_bytes
from .newman_workspace import build_environment
from .postman_folder_extractor import folder_run_name

log = get_logger("ecs_newman_runner")

RUNNER_WORKDIR = "/tmp/job"
# Exit codes of docker/newman-runner/run.mjs besides Newman's own 0/1.
_EXIT_INPUT, _EXIT_UPLOAD, _EXIT_TOO_LARGE = 70, 71, 72

_DETAILS = {
    "invalid_folder": "The selected folder cannot be run.",
    "runner_unavailable": "The Newman execution environment is unavailable.",
    "timeout": "The Newman run exceeded its time limit and was stopped.",
    "cancelled": "The run was cancelled.",
    "resource_limit": "The Newman run exceeded its memory or process limits.",
    "process_failed": "Newman exited unexpectedly.",
    "missing_report": "Newman did not produce a JSON report.",
    "report_too_large": "The Newman report exceeded the maximum allowed size.",
}


def _failed(code: str) -> RunOutcome:
    return RunOutcome(success=False, error_code=code, error_detail=_DETAILS[code])


class LaunchError(Exception):
    """The task could not be started (capacity, image pull, API error)."""


@dataclass(frozen=True)
class TaskStatus:
    stopped: bool
    exit_code: int | None = None
    stop_code: str | None = None
    reason: str | None = None


class TaskLauncher(Protocol):
    def start(self, *, env: dict[str, str], name: str) -> str: ...

    def status(self, handle: str) -> TaskStatus: ...

    def stop(self, handle: str) -> None: ...


class EcsTaskLauncher:
    """Starts the runner task definition on Fargate in the runner subnets."""

    def __init__(self, *, client: Any, settings: Settings) -> None:
        self._ecs = client
        self._settings = settings

    def start(self, *, env: dict[str, str], name: str) -> str:
        s = self._settings
        try:
            resp = self._ecs.run_task(
                cluster=s.ecs_cluster,
                taskDefinition=s.runner_task_definition,
                launchType="FARGATE",
                count=1,
                startedBy="baseline11-worker",
                referenceId=name[:64],
                networkConfiguration={"awsvpcConfiguration": {
                    "subnets": s.runner_subnet_list,
                    "securityGroups": s.runner_security_group_list,
                    "assignPublicIp": "DISABLED",
                }},
                overrides={"containerOverrides": [{
                    "name": s.runner_container_name,
                    "environment": [{"name": k, "value": v} for k, v in env.items()],
                }]},
            )
        except Exception as exc:
            raise LaunchError(type(exc).__name__) from exc
        tasks = resp.get("tasks") or []
        if not tasks:
            reasons = ",".join(str(f.get("reason")) for f in resp.get("failures") or [])
            raise LaunchError(reasons or "no task started")
        return str(tasks[0]["taskArn"])

    def status(self, handle: str) -> TaskStatus:
        resp = self._ecs.describe_tasks(cluster=self._settings.ecs_cluster, tasks=[handle])
        tasks = resp.get("tasks") or []
        if not tasks:
            return TaskStatus(stopped=True, stop_code="TaskFailedToStart", reason="task not found")
        task = tasks[0]
        if task.get("lastStatus") != "STOPPED":
            return TaskStatus(stopped=False)
        container: dict[str, Any] = next(
            (c for c in task.get("containers", []) if c.get("name") == self._settings.runner_container_name), {},
        )
        exit_code = container.get("exitCode")
        reason = " ".join(str(x) for x in (task.get("stoppedReason"), container.get("reason")) if x)
        return TaskStatus(stopped=True, exit_code=exit_code, stop_code=task.get("stopCode"), reason=reason or None)

    def stop(self, handle: str) -> None:
        try:
            self._ecs.stop_task(cluster=self._settings.ecs_cluster, task=handle, reason="baseline11: stopped by worker")
        except Exception:  # noqa: BLE001 - the task may already be gone
            log.warning("stop_task failed", extra={"stage": "newman_run"})


class DockerTaskLauncher:
    """Local stand-in for Fargate: runs the same runner image with local
    Docker and the same limits. Presigned URLs reach the container through
    the process environment, not the docker command line.

    `network` / `url_host_rewrite` are for the local AWS end-to-end only: the
    runner joins the Docker network where the moto S3 server runs, and the
    presigned URLs' host is rewritten to that server's name there (moto does
    not verify signatures; real S3 never sees a rewritten URL)."""

    def __init__(
        self,
        *,
        settings: Settings,
        image: str = "baseline11/newman-runner:6.2.2",
        network: str | None = None,
        url_host_rewrite: tuple[str, str] | None = None,
    ) -> None:
        self._settings = settings
        self._image = image
        self._network = network
        self._rewrite = url_host_rewrite

    def _docker(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self._settings.docker_binary, *args], capture_output=True, text=True, timeout=60,
            env={**os.environ, **(env or {})}, check=False,
        )

    def start(self, *, env: dict[str, str], name: str) -> str:
        if self._rewrite:
            old, new = self._rewrite
            env = {k: v.replace(old, new, 1) if k.endswith("_URL") else v for k, v in env.items()}
        env_flags: list[str] = []
        for key in env:
            env_flags += ["--env", key]  # value inherited from the environment below
        if self._network:
            env_flags += ["--network", self._network]
        result = self._docker(
            "run", "--detach", "--name", name, "--read-only", "--tmpfs", "/tmp:rw,nosuid,size=128m",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--memory", self._settings.newman_container_memory, "--cpus", self._settings.newman_container_cpus,
            "--pids-limit", str(self._settings.newman_container_pids_limit),
            *env_flags, self._image,
            env=env,
        )
        if result.returncode != 0:
            raise LaunchError("docker run failed")
        return name

    def status(self, handle: str) -> TaskStatus:
        result = self._docker("inspect", "--format", "{{.State.Status}} {{.State.ExitCode}} {{.State.OOMKilled}}", handle)
        if result.returncode != 0:
            return TaskStatus(stopped=True, stop_code="TaskFailedToStart", reason="container not found")
        state, code, oom = (result.stdout.strip().split() + ["", "", ""])[:3]
        if state not in ("exited", "dead"):
            return TaskStatus(stopped=False)
        self._docker("rm", "--force", handle)
        return TaskStatus(
            stopped=True, exit_code=int(code) if code.lstrip("-").isdigit() else None,
            stop_code="EssentialContainerExited", reason="OutOfMemoryError" if oom == "true" else None,
        )

    def stop(self, handle: str) -> None:
        self._docker("kill", handle)


class EcsTaskNewmanRunner(NewmanRunner):
    def __init__(
        self,
        settings: Settings,
        *,
        objects: S3ObjectStore,
        launcher: TaskLauncher,
        poll_interval: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._objects = objects
        self._launcher = launcher
        self._poll_interval = poll_interval
        self._clock = clock
        self._sleep = sleep

    def run(self, run_input: RunInput, *, should_cancel: Callable[[], bool]) -> RunOutcome:
        folder_name: str | None = None
        if run_input.folder_id is not None:
            try:
                folder_name = folder_run_name(run_input.collection_data, run_input.folder_id)
            except (KeyError, ValueError):
                return _failed("invalid_folder")

        run_id = f"run_{secrets.token_hex(6)}"
        input_key = run_input_key(run_input.job_id, run_id)
        report_key = run_report_key(run_input.job_id, run_id)
        try:
            self._objects.put_json(input_key, {
                "collection": run_input.collection_data,
                "environment": build_environment(run_input.environment_data, run_input.supplied_values),
                "args": newman_run_args(
                    settings=self._settings, workdir=RUNNER_WORKDIR, folder_name=folder_name,
                    timeout_seconds=run_input.timeout_seconds,
                ),
            })
            expires = run_input.timeout_seconds + self._settings.presign_grace_seconds
            env = {
                "RUN_INPUT_URL": self._objects.presigned_get(input_key, expires_seconds=expires),
                "REPORT_UPLOAD_URL": self._objects.presigned_put(report_key, expires_seconds=expires),
                "MAX_REPORT_BYTES": str(self._settings.max_report_bytes),
            }
            try:
                handle = self._launcher.start(env=env, name=f"b11-{run_id}")
            except LaunchError as exc:
                log.warning(f"runner task launch failed: {exc}", extra={"stage": "newman_run"})
                return _failed("runner_unavailable")
            status = self._supervise(handle, run_input.timeout_seconds, should_cancel)
            if isinstance(status, RunOutcome):
                return status
            return self._outcome(status, report_key)
        finally:
            for key in (input_key, report_key):
                try:
                    self._objects.delete(key)
                except Exception:  # noqa: BLE001 - lifecycle rule removes leftovers
                    log.warning("could not delete run object", extra={"stage": "newman_run"})

    def _supervise(
        self, handle: str, timeout_seconds: int, should_cancel: Callable[[], bool],
    ) -> TaskStatus | RunOutcome:
        deadline = self._clock() + timeout_seconds + self._settings.newman_start_grace_seconds
        while True:
            status = self._launcher.status(handle)
            if status.stopped:
                return status
            if should_cancel():
                self._launcher.stop(handle)
                return _failed("cancelled")
            if self._clock() >= deadline:
                self._launcher.stop(handle)
                return _failed("timeout")
            self._sleep(self._poll_interval)

    def _outcome(self, status: TaskStatus, report_key: str) -> RunOutcome:
        reason = status.reason or ""
        if status.stop_code == "TaskFailedToStart":
            log.warning("runner task failed to start", extra={"stage": "newman_run"})
            return _failed("runner_unavailable")
        if "OutOfMemory" in reason or status.exit_code == 137:
            return _failed("resource_limit")
        if status.exit_code in (_EXIT_INPUT, _EXIT_UPLOAD):
            return _failed("runner_unavailable")
        if status.exit_code == _EXIT_TOO_LARGE:
            return _failed("report_too_large")
        if status.exit_code not in (0, 1):  # 1 = assertions failed; still a complete run
            return _failed("process_failed")
        try:
            data = self._objects.get_bytes(report_key, max_bytes=self._settings.max_report_bytes)
        except ObjectTooLarge:
            return _failed("report_too_large")
        if data is None:
            return _failed("missing_report")
        return validate_report_bytes(data)
