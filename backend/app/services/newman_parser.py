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

import json
from dataclasses import dataclass

from ..core.config import Settings
from ..core.errors import ProblemException, validation_error


@dataclass
class ParsedReport:
    data: dict
    warnings: list[str]


def _decode_json(raw: bytes) -> object:
    text = raw.decode("utf-8-sig")  # tolerate UTF-8 BOM
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:  # precise location
        raise validation_error(
            "File is not valid JSON.",
            errors=[{"path": "$", "detail": f"JSON parse error at line {exc.lineno}, column {exc.colno}: {exc.msg}"}],
        ) from exc


def _check_complexity(node: object, settings: Settings) -> None:
    """Bounded traversal enforcing depth/size limits; raises on violation."""
    total = 0
    stack: list[tuple[object, int]] = [(node, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > settings.max_json_depth:
            raise validation_error(
                "JSON nesting is too deep.",
                errors=[{"path": "$", "detail": f"Depth exceeds limit ({settings.max_json_depth})."}],
            )
        if isinstance(current, dict):
            total += len(current)
            for v in current.values():
                stack.append((v, depth + 1))
        elif isinstance(current, list):
            if len(current) > settings.max_array_length:
                raise validation_error(
                    "JSON array is too large.",
                    errors=[{"path": "$", "detail": f"Array length exceeds limit ({settings.max_array_length})."}],
                )
            total += len(current)
            for v in current:
                stack.append((v, depth + 1))
        elif isinstance(current, str):
            if len(current) > settings.max_scalar_length:
                raise validation_error(
                    "A JSON string value is too large.",
                    errors=[{"path": "$", "detail": f"String length exceeds limit ({settings.max_scalar_length})."}],
                )
        if total > settings.max_total_values:
            raise validation_error(
                "JSON document has too many values.",
                errors=[{"path": "$", "detail": f"Total values exceed limit ({settings.max_total_values})."}],
            )


def parse_report(raw: bytes, *, filename: str, settings: Settings) -> ParsedReport:
    if len(raw) > settings.max_file_bytes:
        raise ProblemException(
            status=413, code="payload_too_large", title="Payload too large",
            detail=f"'{filename}' exceeds the {settings.max_file_bytes} byte limit.",
        )

    data = _decode_json(raw)
    if not isinstance(data, dict):
        raise validation_error(
            "Root of a Newman report must be a JSON object.",
            errors=[{"path": "$", "detail": "Expected an object with a 'run' key."}],
        )

    _check_complexity(data, settings)

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
