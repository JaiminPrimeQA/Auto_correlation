"""Run one Newman execution in an isolated, short-lived Docker container.

Implements the `NewmanRunner` Protocol. Each call gets its own container
name and its own private workspace (deleted afterwards), so run B can never
see run A's files or mutations (spec §3 steps 10-11). The process runs
without a shell and with stdout/stderr discarded; the runner enforces a
wall-clock deadline and polls `should_cancel`, killing the container on
either (Phase 2 contract). Every failure maps to a stable error code with a
fixed, user-safe message.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from ..core.config import Settings
from ..core.logging import get_logger
from ..domain.execution_job import NewmanRunner, RunInput, RunOutcome
from .newman_command import build_docker_argv
from .newman_report_adapter import read_generated_report
from .newman_workspace import newman_workspace
from .postman_folder_extractor import folder_run_name

log = get_logger("docker_newman_runner")

_KILL_TIMEOUT_SECONDS = 30
# Default for `container_user`: run as this process's own uid:gid (POSIX).
# Passing None explicitly means "no --user flag" (the image's `node` user).
PROCESS_USER = "__process_user__"
_DETAILS = {
    "invalid_folder": "The selected folder cannot be run.",
    "invalid_destination": "A validated request target could not be pinned for execution.",
    "runner_unavailable": "The Newman execution environment is unavailable.",
    "timeout": "The Newman run exceeded its time limit and was stopped.",
    "cancelled": "The run was cancelled.",
    "resource_limit": "The Newman run exceeded its memory or process limits.",
    "process_failed": "Newman exited unexpectedly.",
}


def _failed(code: str) -> RunOutcome:
    return RunOutcome(success=False, error_code=code, error_detail=_DETAILS[code])


def default_container_user() -> str | None:
    """Run as the API process's own uid:gid on POSIX so the 0600 workspace
    files are readable in the container; None (the image's non-root `node`
    user) where uids don't apply (Docker Desktop on Windows)."""
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    if getuid is not None and getgid is not None:
        return f"{getuid()}:{getgid()}"
    return None


class DockerNewmanRunner(NewmanRunner):
    def __init__(
        self,
        settings: Settings,
        *,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
        kill_container: Callable[[str], None] | None = None,
        workspace_base: Path | None = None,
        poll_interval: float = 0.5,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        container_user: str | None = PROCESS_USER,
    ) -> None:
        self._settings = settings
        self._popen = popen
        self._kill_container = kill_container or self._docker_kill
        self._workspace_base = workspace_base
        self._poll_interval = poll_interval
        self._clock = clock
        self._sleep = sleep
        self._container_user = default_container_user() if container_user == PROCESS_USER else container_user

    def run(self, run_input: RunInput, *, should_cancel: Callable[[], bool]) -> RunOutcome:
        folder_name: str | None = None
        if run_input.folder_id is not None:
            try:
                folder_name = folder_run_name(run_input.collection_data, run_input.folder_id)
            except (KeyError, ValueError):
                return _failed("invalid_folder")

        container_name = f"b11-newman-{secrets.token_hex(8)}"
        with newman_workspace(run_input, base_dir=self._workspace_base) as workspace:
            try:
                argv = build_docker_argv(
                    settings=self._settings,
                    container_name=container_name,
                    workspace_root=workspace.root,
                    folder_name=folder_name,
                    host_pins=run_input.host_pins,
                    container_user=self._container_user,
                    timeout_seconds=run_input.timeout_seconds,
                )
            except ValueError:
                return _failed("invalid_destination")
            try:
                process = self._popen(
                    argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    shell=False,
                )
            except OSError:
                log.warning("newman run failed: runner_unavailable (docker could not be started)",
                            extra={"stage": "newman_run"})
                return _failed("runner_unavailable")

            failure = self._supervise(process, container_name, run_input.timeout_seconds, should_cancel)
            if failure is not None:
                log.warning(f"newman run failed: {failure.error_code}", extra={"stage": "newman_run"})
                return failure
            return read_generated_report(workspace.report_path, max_bytes=self._settings.max_report_bytes)

    def _supervise(
        self, process: subprocess.Popen, container_name: str, timeout_seconds: int,
        should_cancel: Callable[[], bool],
    ) -> RunOutcome | None:
        deadline = self._clock() + timeout_seconds + self._settings.newman_start_grace_seconds
        while True:
            exit_code = process.poll()
            if exit_code is not None:
                return self._map_exit_code(exit_code)
            if should_cancel():
                self._stop(process, container_name)
                return _failed("cancelled")
            if self._clock() >= deadline:
                self._stop(process, container_name)
                return _failed("timeout")
            self._sleep(self._poll_interval)

    @staticmethod
    def _map_exit_code(exit_code: int) -> RunOutcome | None:
        if exit_code in (0, 1):
            return None  # 1 = assertions failed; the report is still complete
        if exit_code == 125:
            return _failed("runner_unavailable")
        if exit_code == 137:
            return _failed("resource_limit")
        return _failed("process_failed")

    def _stop(self, process: subprocess.Popen, container_name: str) -> None:
        self._kill_container(container_name)
        try:
            process.wait(timeout=_KILL_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()

    def _docker_kill(self, container_name: str) -> None:
        try:
            subprocess.run(
                [self._settings.docker_binary, "kill", container_name],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=_KILL_TIMEOUT_SECONDS, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            log.warning("docker kill failed", extra={"stage": "newman_run"})
