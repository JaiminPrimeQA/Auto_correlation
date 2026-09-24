"""Evaluate the JSONPath subset emitted by this app without executing code.

Supports object members, array indices/wildcards and equality filters. Unknown
syntax is rejected; JMeter remains the runtime authority for JSONPath behavior.
"""

from __future__ import annotations

import ast
import json
import re

_STRING = r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\""
_STEP = re.compile(rf"\.([A-Za-z_]\w*)|\[({_STRING})\]|\[(\d+|\*)\]")
_FILTER = re.compile(
    rf"\[\?\(\s*@(?P<field>\.[A-Za-z_]\w*|\[(?:{_STRING})\])\s*==\s*"
    rf"(?P<value>{_STRING}|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null)\s*\)\]"
)


def _literal(text: str) -> object:
    return ast.literal_eval(text) if text.startswith("'") else json.loads(text)


def jsonpath_values(body: object, expression: str) -> list[object]:
    if not expression.startswith("$"):
        return []
    nodes = [body]
    offset = 1
    while offset < len(expression):
        filt = _FILTER.match(expression, offset)
        step = _STEP.match(expression, offset)
        out = []
        if filt:
            try:
                expected = _literal(filt.group("value"))
            except (ValueError, SyntaxError):
                return []
            for node in nodes:
                if isinstance(node, list):
                    for item in node:
                        values = jsonpath_values(item, "$" + filt.group("field"))
                        numeric = (len(values) == 1 and type(values[0]) in (int, float) and type(expected) in (int, float))
                        if len(values) == 1 and (type(values[0]) is type(expected) or numeric) and values[0] == expected:
                            out.append(item)
            offset = filt.end()
        elif step:
            try:
                key = step.group(1) or (_literal(step.group(2)) if step.group(2) else None)
            except (ValueError, SyntaxError):
                return []
            index = step.group(3)
            for node in nodes:
                if key is not None and isinstance(node, dict) and key in node:
                    out.append(node[key])
                elif index is not None and isinstance(node, list):
                    if index == "*":
                        out.extend(node)
                    elif int(index) < len(node):
                        out.append(node[int(index)])
            offset = step.end()
        else:
            return []
        nodes = out
    return nodes


def scalar_text(value: object) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
