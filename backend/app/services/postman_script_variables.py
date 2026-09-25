"""Find variable names that collection scripts assign at runtime.

A producer request's test script typically stores a value for later
requests (`pm.environment.set("token", ...)`). Those names have no static
value before execution but are not missing either: Newman fills them during
the run (spec §3 step 11). This is a static, best-effort scan of script
source; it never executes anything.
"""

from __future__ import annotations

import re

_SETTER = re.compile(
    r"""(?:pm\.(?:environment|collectionVariables|globals|variables)\.set"""
    r"""|postman\.set(?:Environment|Global)Variable)"""
    r"""\s*\(\s*(['"`])([^'"`\n]+)\1"""
)


def _script_text(event: object) -> str:
    if not isinstance(event, dict):
        return ""
    script = event.get("script")
    if not isinstance(script, dict):
        return ""
    exec_lines = script.get("exec")
    if isinstance(exec_lines, list):
        return "\n".join(line for line in exec_lines if isinstance(line, str))
    return exec_lines if isinstance(exec_lines, str) else ""


def _names_in_events(node: dict) -> set[str]:
    names: set[str] = set()
    events = node.get("event")
    for event in events if isinstance(events, list) else []:
        names.update(m.group(2).strip() for m in _SETTER.finditer(_script_text(event)))
    return names


def _walk(items: object) -> set[str]:
    names: set[str] = set()
    if not isinstance(items, list):
        return names
    for item in items:
        if not isinstance(item, dict):
            continue
        names |= _names_in_events(item)
        names |= _walk(item.get("item"))
    return names


def script_set_variable_names(collection_data: dict) -> set[str]:
    return _names_in_events(collection_data) | _walk(collection_data.get("item"))
