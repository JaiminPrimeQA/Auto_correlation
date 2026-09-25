"""Apply Postman variable precedence and classify sensitivity.

Precedence (highest to lowest): supplied runtime value, environment value,
collection-level variable. A name starting with '$' is a Postman built-in
dynamic variable (e.g. {{$guid}}) - Newman resolves these itself at
execution time, so they are never reported as unresolved. A name with no
static value that a collection script sets at runtime is reported as
`script`, not `unresolved`.
"""

from __future__ import annotations

import json

from ..domain.enums import VariableSource
from ..domain.postman_models import PostmanVariable
from ..utils.masking import is_sensitive_key
from .postman_variable_extractor import VariableReference


def resolve_variables(
    references: list[VariableReference],
    *,
    collection_variables: dict[str, str],
    environment_values: dict[str, str],
    supplied: dict[str, str] | None = None,
    script_set: set[str] | None = None,
) -> list[PostmanVariable]:
    supplied = supplied or {}
    script_set = script_set or set()
    locations_by_name: dict[str, list[str]] = {}
    for ref in references:
        locations_by_name.setdefault(ref.name, []).append(ref.location)

    variables: list[PostmanVariable] = []
    for name, locations in locations_by_name.items():
        if name.startswith("$"):
            source = VariableSource.DYNAMIC
        elif name in supplied:
            source = VariableSource.SUPPLIED
        elif name in environment_values:
            source = VariableSource.ENVIRONMENT
        elif name in collection_variables:
            source = VariableSource.COLLECTION
        elif name in script_set:
            source = VariableSource.SCRIPT
        else:
            source = VariableSource.UNRESOLVED
        variables.append(PostmanVariable(
            name=name,
            source=source,
            sensitive=is_sensitive_key(name),
            locations=sorted(dict.fromkeys(locations)),
        ))
    return sorted(variables, key=lambda v: v.name)


def unresolved_names(variables: list[PostmanVariable]) -> list[str]:
    return sorted(v.name for v in variables if v.source == VariableSource.UNRESOLVED)


def _canonical_value(value: object) -> str:
    """Canonicalize a collection variable value the same way supplied values
    are: strings pass through, booleans/numbers read as they would inline in
    JSON (`True` -> "true", `1` -> "1"), and a missing/null value is "".
    Anything else (a hand-edited object/array) falls back to `str()`.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (bool, int, float)):
        return json.dumps(value)
    return str(value)


def collection_variable_values(collection_data: dict) -> dict[str, str]:
    """`{key: canonical value}` for every collection-level variable with a
    non-empty key. Shared by the inspector and the execution-job service.
    """
    return {
        key: _canonical_value(v.get("value"))
        for v in (collection_data.get("variable") or [])
        if isinstance(v, dict) and isinstance(key := v.get("key"), str) and key
    }
