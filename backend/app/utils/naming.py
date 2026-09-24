"""JMeter variable-name sanitisation, field-name aliasing and collision handling."""

from __future__ import annotations

import re

_INVALID = re.compile(r"[^A-Za-z0-9_]")
_VALID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# --------------------------------------------------------------------------- #
# Field-name normalisation and aliasing
#
# Producer and consumer keys are frequently the same concept written
# differently (merchantGUID vs merchantsGuid vs merchant_guid). We normalise by
# lowercasing and stripping non-alphanumerics, then resolve through an explicit
# alias map. Unlisted spellings stay distinct; removing trailing "s" could
# damage names such as statusId.
# --------------------------------------------------------------------------- #

# Explicit, extend-here alias map: normalised key -> canonical concept.
FIELD_ALIASES: dict[str, str] = {
    "merchantguid": "merchantguid",
    "merchantsguid": "merchantguid",
    "merchantuuid": "merchantguid",
    "merchantid": "merchantid",
    "merchantsid": "merchantid",
    "webhookid": "webhookid",
    "webhookguid": "webhookguid",
    "accesstoken": "accesstoken",
    "bearertoken": "accesstoken",
    "authtoken": "accesstoken",
}

# Placeholder / example values that stand in for a real dynamic value. When a
# consumer field carries one of these AND its name matches a unique earlier
# producer, it is a correlation the exact-value matcher would otherwise miss.
PLACEHOLDER_VALUES: frozenset[str] = frozenset(
    {"string", "<string>", "{{string}}", "guid", "uuid", "id", "0", "null", "undefined", "none",
     "00000000-0000-0000-0000-000000000000"}
)


def normalize_field_name(name: str | None) -> str:
    """Lowercase, strip non-alphanumerics, resolve through the alias map."""
    if not name:
        return ""
    normalized = re.sub(r"[^a-z0-9]", "", name.lower())
    return FIELD_ALIASES.get(normalized, normalized)


def fields_are_equivalent(left: str | None, right: str | None) -> bool:
    """True when names are equal after normalization and explicit aliases."""
    a = normalize_field_name(left)
    b = normalize_field_name(right)
    if not a or not b:
        return False
    if a == b:
        return True
    return False


def is_placeholder_value(value: str | None) -> bool:
    """True when a consumer value is a placeholder/example rather than real data."""
    if value is None:
        return False
    return value.strip().lower() in PLACEHOLDER_VALUES


def is_valid_variable_name(name: str) -> bool:
    return bool(_VALID.match(name))


def sanitize_variable_name(raw: str, *, fallback: str = "var") -> str:
    """Produce a valid JMeter variable identifier ``[A-Za-z_][A-Za-z0-9_]*``."""
    if not raw:
        return fallback
    name = _INVALID.sub("_", raw.strip())
    name = re.sub(r"_+", "_", name).strip("_")
    if not name:
        name = fallback
    if name[0].isdigit():
        name = f"{fallback}_{name}"
    return name


def dedupe_variable_name(name: str, taken: set[str]) -> str:
    """Return a unique variable name given a set of already-used names."""
    if name not in taken:
        taken.add(name)
        return name
    i = 2
    while f"{name}_{i}" in taken:
        i += 1
    unique = f"{name}_{i}"
    taken.add(unique)
    return unique


def suggest_variable_name(key: str | None, canonical_path: str) -> str:
    """Derive a readable variable name from a producer location."""
    base = key
    if not base and canonical_path:
        # take last JSONPath segment or header/query key
        seg = re.split(r"[.\[\]/]", canonical_path.rstrip("]"))
        seg = [s for s in seg if s and s not in ("$", "")]
        base = seg[-1] if seg else canonical_path
    base = base or "value"
    return sanitize_variable_name(base)
