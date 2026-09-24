"""Parse and validate Newman JSON reporter files.

Responsibilities:
  * Parse JSON strictly as UTF-8 (tolerating a leading BOM).
  * Enforce complexity bounds (depth, array length, scalar length, total values).
  * Verify the expected Newman shape: ``run.executions`` is a non-empty array.
  * Emit precise, path-based validation errors for unrelated JSON.

This module NEVER executes scripts, evaluates URLs, or makes outbound requests.
It only reads structure.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.config import Settings
from ..core.errors import ProblemException, validation_error
from ..utils.json_safety import check_json_complexity, decode_json


@dataclass
class ParsedReport:
    data: dict
    warnings: list[str]


def parse_report(raw: bytes, *, filename: str, settings: Settings) -> ParsedReport:
    if len(raw) > settings.max_file_bytes:
        raise ProblemException(
            status=413, code="payload_too_large", title="Payload too large",
            detail=f"'{filename}' exceeds the {settings.max_file_bytes} byte limit.",
        )

    data = decode_json(raw)
    if not isinstance(data, dict):
        raise validation_error(
            "Root of a Newman report must be a JSON object.",
            errors=[{"path": "$", "detail": "Expected an object with a 'run' key."}],
        )

    check_json_complexity(data, settings)

    warnings: list[str] = []
    run = data.get("run")
    if not isinstance(run, dict):
        raise validation_error(
            "Not a Newman report: missing 'run' object.",
            errors=[{"path": "$.run", "detail": "Expected an object."}],
        )
    executions = run.get("executions")
    if not isinstance(executions, list) or not executions:
        raise validation_error(
            "Not a Newman report: 'run.executions' must be a non-empty array.",
            errors=[{"path": "$.run.executions", "detail": "Expected a non-empty array."}],
        )

    # Soft structural checks -> warnings (retain response-less executions).
    without_response = sum(1 for e in executions if not isinstance(e, dict) or "response" not in e)
    if without_response:
        warnings.append(
            f"{without_response} of {len(executions)} executions have no response; "
            "they are retained but cannot act as correlation producers."
        )
    if not any(isinstance(e, dict) and "request" in e for e in executions):
        raise validation_error(
            "Not a Newman report: no execution contains a 'request'.",
            errors=[{"path": "$.run.executions[*].request", "detail": "At least one request is required."}],
        )

    return ParsedReport(data=data, warnings=warnings)
