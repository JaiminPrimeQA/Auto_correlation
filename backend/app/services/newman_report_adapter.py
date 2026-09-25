"""Turn a Newman JSON reporter file into a RunOutcome.

Only structural checks happen here (present, within the size limit, JSON,
has `run.executions`). Whether the run *succeeded as a business flow* is the
existing run-health analysis's job - an assertion failure is still a valid
report (spec §4). Error details are fixed messages; report content is never
echoed.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..domain.execution_job import RunOutcome


def _failed(code: str, detail: str) -> RunOutcome:
    return RunOutcome(success=False, error_code=code, error_detail=detail)


def read_generated_report(path: Path, *, max_bytes: int) -> RunOutcome:
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return _failed("missing_report", "Newman did not produce a JSON report.")
    if size == 0:
        return _failed("missing_report", "Newman did not produce a JSON report.")
    if size > max_bytes:
        return _failed("report_too_large", "The Newman report exceeded the maximum allowed size.")
    return validate_report_bytes(path.read_bytes())


def validate_report_bytes(data: bytes) -> RunOutcome:
    """Structural checks on report bytes already within the size limit."""
    if not data:
        return _failed("missing_report", "Newman did not produce a JSON report.")
    try:
        document = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return _failed("malformed_report", "Newman produced a report that is not valid JSON.")
    run = document.get("run") if isinstance(document, dict) else None
    if not isinstance(run, dict) or not isinstance(run.get("executions"), list):
        return _failed("malformed_report", "Newman produced a report without run executions.")
    return RunOutcome(success=True, report_bytes=data)
