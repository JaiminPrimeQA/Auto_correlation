"""Build the `docker run` argument array for one isolated Newman run.

Always an argument list for `subprocess.Popen(..., shell=False)` - never a
command string (spec §5.1 item 5). Nothing here carries a variable value:
supplied values live in the workspace's environment file.

Isolation (spec §8): read-only root filesystem with a small /tmp tmpfs, all
capabilities dropped, no-new-privileges, CPU/memory/pid/file-size limits,
the per-run workspace as the only mount, no Docker socket, no host network.
DNS is pointed at 127.0.0.1 (nothing answers), so the container can reach
only hostnames pinned with --add-host to the addresses destination
validation checked - a name can never re-resolve somewhere else.
"""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path

from ..core.config import MIB, Settings
from .newman_workspace import CONTAINER_WORKDIR

_CONTAINER_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
_HOSTNAME = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$")


def _add_host_flags(host_pins: dict[str, list[str]]) -> list[str]:
    flags: list[str] = []
    for host in sorted(host_pins):
        if not _HOSTNAME.match(host):
            raise ValueError("Invalid pinned hostname.")
        for address in sorted(host_pins[host]):
            ipaddress.ip_address(address)  # ValueError if not an IP literal
            flags += ["--add-host", f"{host}:{address}"]
    return flags


def build_docker_argv(
    *,
    settings: Settings,
    container_name: str,
    workspace_root: Path,
    folder_name: str | None,
    host_pins: dict[str, list[str]],
    container_user: str | None,
    timeout_seconds: int,
) -> list[str]:
    if not _CONTAINER_NAME.match(container_name):
        raise ValueError("Invalid container name.")
    if "," in str(workspace_root):
        # `--mount` is a comma-separated spec; a comma would inject mount options.
        raise ValueError("Workspace path must not contain a comma.")
    argv = [
        settings.docker_binary, "run", "--rm",
        "--name", container_name,
        "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", str(settings.newman_container_pids_limit),
        "--memory", settings.newman_container_memory,
        "--cpus", settings.newman_container_cpus,
        "--ulimit", f"fsize={settings.max_report_bytes + 8 * MIB}",
        "--network", "bridge",
        "--dns", "127.0.0.1",
        "--env", "HOME=/tmp",
        "--mount", f"type=bind,src={workspace_root},dst={CONTAINER_WORKDIR}",
        "--workdir", CONTAINER_WORKDIR,
    ]
    if container_user:
        argv += ["--user", container_user]
    argv += _add_host_flags(host_pins)
    argv.append(settings.newman_docker_image)
    argv += newman_run_args(
        settings=settings, workdir=CONTAINER_WORKDIR, folder_name=folder_name, timeout_seconds=timeout_seconds,
    )
    return argv


def newman_run_args(*, settings: Settings, workdir: str, folder_name: str | None, timeout_seconds: int) -> list[str]:
    """The `newman ...` arguments shared by the Docker runner and the Fargate
    runner task: fixed input/report paths under `workdir`, JSON reporter,
    hard timeouts, no redirects, no file reads outside the working dir."""
    args = [
        "run", f"{workdir}/collection.json",
        "--environment", f"{workdir}/environment.json",
        "--reporters", "json",
        "--reporter-json-export", f"{workdir}/out/report.json",
        "--timeout", str(timeout_seconds * 1000),
        "--timeout-request", str(settings.newman_request_timeout_ms),
        "--working-dir", f"{workdir}/files",
        "--no-insecure-file-read",
        "--ignore-redirects",
        "--disable-unicode",
        "--color", "off",
    ]
    if folder_name is not None:
        # One `--folder=<name>` element: a name starting with '-' can never be
        # read as a separate Newman option.
        args.append(f"--folder={folder_name}")
    return args
