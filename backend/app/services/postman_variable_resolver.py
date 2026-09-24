"""Apply Postman variable precedence and classify sensitivity.

Precedence (highest to lowest): supplied runtime value, environment value,
collection-level variable. A name starting with '$' is a Postman built-in
dynamic variable (e.g. {{$guid}}) - Newman resolves these itself at
execution time, so they are never reported as unresolved.
"""

from __future__ import annotations

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
) -> list[PostmanVariable]:
    supplied = supplied or {}
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


def collection_variable_values(collection_data: dict) -> dict[str, str]:
    """`{key: str(value)}` for every collection-level variable with a non-empty key."""
    return {
        key: str(v.get("value", ""))
        for v in (collection_data.get("variable") or [])
        if isinstance(v, dict) and isinstance(key := v.get("key"), str) and key
    }
