"""Shared bounded-traversal safety checks for untrusted JSON documents.

Used by every parser that accepts user-uploaded JSON (Newman reports, Postman
collections, Postman environments) so complexity limits are enforced
identically everywhere.
"""

from __future__ import annotations

import json

from ..core.config import Settings
from ..core.errors import validation_error


def decode_json(raw: bytes) -> object:
    text = raw.decode("utf-8-sig")  # tolerate UTF-8 BOM
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:  # precise location
        raise validation_error(
            "File is not valid JSON.",
            errors=[{"path": "$", "detail": f"JSON parse error at line {exc.lineno}, column {exc.colno}: {exc.msg}"}],
        ) from exc


def check_json_complexity(node: object, settings: Settings) -> None:
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
