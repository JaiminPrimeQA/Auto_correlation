"""A private, per-run working directory for one Newman execution.

Supplied variable values reach Newman only through `environment.json` in
this directory - never through process arguments (spec §8). The directory is
created 0700, its input files 0600, and it is always deleted when the run
ends, however it ends.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ..domain.execution_job import RunInput

CONTAINER_WORKDIR = "/job"
_DEFAULT_ENV_NAME = "Baseline11 run"


@dataclass(frozen=True)
class NewmanWorkspace:
    root: Path
    collection_path: Path
    environment_path: Path
    report_path: Path
    files_dir: Path


def build_environment(environment_data: dict | None, supplied_values: dict[str, str]) -> dict:
    """The uploaded environment with every supplied value applied on top
    (supplied wins, and a disabled entry is re-enabled when supplied)."""
    source = environment_data or {}
    values = [copy.deepcopy(v) for v in (source.get("values") or []) if isinstance(v, dict)]
    by_key = {v.get("key"): v for v in values}
    for key, value in supplied_values.items():
        existing = by_key.get(key)
        if existing is not None:
            existing["value"] = value
            existing["enabled"] = True
        else:
            values.append({"key": key, "value": value, "enabled": True, "type": "default"})
    return {"name": source.get("name") or _DEFAULT_ENV_NAME, "values": values}


def _write_private(path: Path, document: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(document, fh)


@contextmanager
def newman_workspace(run_input: RunInput, *, base_dir: Path | None = None) -> Iterator[NewmanWorkspace]:
    root = Path(tempfile.mkdtemp(prefix="b11-newman-", dir=base_dir))
    try:
        os.chmod(root, 0o700)
        out_dir = root / "out"
        files_dir = root / "files"
        out_dir.mkdir(mode=0o700)
        files_dir.mkdir(mode=0o700)
        workspace = NewmanWorkspace(
            root=root,
            collection_path=root / "collection.json",
            environment_path=root / "environment.json",
            report_path=out_dir / "report.json",
            files_dir=files_dir,
        )
        _write_private(workspace.collection_path, run_input.collection_data)
        _write_private(
            workspace.environment_path,
            build_environment(run_input.environment_data, run_input.supplied_values),
        )
        yield workspace
    finally:
        shutil.rmtree(root, ignore_errors=True)
