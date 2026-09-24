# backend/app/services/postman_parser.py
"""Parse and validate Postman Collection v2.0/2.1 and Postman Environment JSON.

Structural validation only - this module never executes requests, evaluates
scripts, or makes outbound network calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.config import Settings
from ..core.errors import ProblemException, validation_error
from ..utils.json_safety import check_json_complexity, decode_json


@dataclass
class ParsedCollection:
    data: dict
    warnings: list[str] = field(default_factory=list)


@dataclass
class ParsedEnvironment:
    data: dict
    values: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def parse_collection(raw: bytes, *, filename: str, settings: Settings) -> ParsedCollection:
    if len(raw) > settings.max_collection_bytes:
        raise ProblemException(
            status=413, code="payload_too_large", title="Payload too large",
            detail=f"'{filename}' exceeds the {settings.max_collection_bytes} byte collection limit.",
        )
    data = decode_json(raw)
    if not isinstance(data, dict):
        raise validation_error(
            "Root of a Postman collection must be a JSON object.",
            errors=[{"path": "$", "detail": "Expected an object with 'info' and 'item'."}],
        )
    check_json_complexity(data, settings)

    info = data.get("info")
    if not isinstance(info, dict) or not info.get("name"):
        raise validation_error(
            "Not a Postman collection: missing 'info.name'.",
            errors=[{"path": "$.info.name", "detail": "Expected a non-empty string."}],
        )
    items = data.get("item")
    if not isinstance(items, list):
        raise validation_error(
            "Not a Postman collection: 'item' must be an array.",
            errors=[{"path": "$.item", "detail": "Expected an array."}],
        )

    warnings: list[str] = []
    schema = info.get("schema", "")
    if not isinstance(schema, str) or "collection/v2" not in schema:
        warnings.append("Collection schema version could not be confirmed as v2.0 or v2.1; proceeding best-effort.")
    if not items:
        warnings.append("Collection has no requests.")
    return ParsedCollection(data=data, warnings=warnings)


def parse_environment(raw: bytes, *, filename: str, settings: Settings) -> ParsedEnvironment:
    if len(raw) > settings.max_environment_bytes:
        raise ProblemException(
            status=413, code="payload_too_large", title="Payload too large",
            detail=f"'{filename}' exceeds the {settings.max_environment_bytes} byte environment limit.",
        )
    data = decode_json(raw)
    if not isinstance(data, dict):
        raise validation_error(
            "Root of a Postman environment must be a JSON object.",
            errors=[{"path": "$", "detail": "Expected an object with a 'values' array."}],
        )
    check_json_complexity(data, settings)

    entries = data.get("values")
    if not isinstance(entries, list):
        raise validation_error(
            "Not a Postman environment: 'values' must be an array.",
            errors=[{"path": "$.values", "detail": "Expected an array."}],
        )

    values: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("key"), str) or not entry.get("key"):
            continue
        if entry.get("enabled", True) is False:
            continue
        values[entry["key"]] = str(entry.get("value", ""))

    warnings: list[str] = []
    if not values:
        warnings.append("Environment has no enabled values.")
    return ParsedEnvironment(data=data, values=values, warnings=warnings)
