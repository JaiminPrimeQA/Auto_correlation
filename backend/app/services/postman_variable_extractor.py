"""Find every {{variable}} reference in a Postman collection's requests.

Pure string scanning - never evaluates scripts or resolves values. Postman's
built-in dynamic variables (e.g. {{$guid}}) are returned like any other
reference; `postman_variable_resolver` (Task 8) is what classifies them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_VAR = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")


@dataclass
class VariableReference:
    name: str
    location: str


def _names_in(text: object) -> list[str]:
    return _VAR.findall(text) if isinstance(text, str) else []


def _from_url(url: object, location: str) -> list[VariableReference]:
    refs: list[VariableReference] = []
    if isinstance(url, str):
        refs += [VariableReference(n, f"{location} > url") for n in _names_in(url)]
    elif isinstance(url, dict):
        refs += [VariableReference(n, f"{location} > url") for n in _names_in(url.get("raw"))]
        for part_key in ("host", "path"):
            for segment in url.get(part_key, []) or []:
                refs += [VariableReference(n, f"{location} > url.{part_key}") for n in _names_in(segment)]
        for q in url.get("query", []) or []:
            if isinstance(q, dict):
                refs += [VariableReference(n, f"{location} > url.query") for n in _names_in(q.get("value"))]
        for v in url.get("variable", []) or []:
            if isinstance(v, dict):
                refs += [VariableReference(n, f"{location} > url.variable") for n in _names_in(str(v.get("value", "")))]
    return refs


def _from_headers(headers: object, location: str) -> list[VariableReference]:
    refs: list[VariableReference] = []
    if not isinstance(headers, list):
        return refs
    for h in headers:
        if isinstance(h, dict):
            refs += [VariableReference(n, f"{location} > header") for n in _names_in(h.get("value"))]
    return refs


def _from_body(body: object, location: str) -> list[VariableReference]:
    if not isinstance(body, dict):
        return []
    refs: list[VariableReference] = []
    mode = body.get("mode")
    if mode == "raw":
        refs += [VariableReference(n, f"{location} > body") for n in _names_in(body.get("raw"))]
    elif mode in ("urlencoded", "formdata"):
        for param in body.get(mode, []) or []:
            if isinstance(param, dict) and param.get("type") != "file":
                refs += [VariableReference(n, f"{location} > body") for n in _names_in(str(param.get("value", "")))]
    return refs


def _from_auth(auth: object, location: str) -> list[VariableReference]:
    if not isinstance(auth, dict):
        return []
    refs: list[VariableReference] = []
    for key, params in auth.items():
        if key == "type" or not isinstance(params, list):
            continue
        for p in params:
            if isinstance(p, dict):
                refs += [VariableReference(n, f"{location} > auth") for n in _names_in(str(p.get("value", "")))]
    return refs


def _walk(items: list, path: str) -> list[VariableReference]:
    refs: list[VariableReference] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        here = f"{path} > {it.get('name', 'unnamed')}" if path else it.get("name", "unnamed")
        if isinstance(it.get("item"), list):
            refs.extend(_walk(it["item"], here))
            continue
        request = it.get("request")
        if not isinstance(request, dict):
            continue
        refs.extend(_from_url(request.get("url"), here))
        refs.extend(_from_headers(request.get("header"), here))
        refs.extend(_from_body(request.get("body"), here))
        refs.extend(_from_auth(request.get("auth"), here))
    return refs


def extract_variable_references(collection_data: dict) -> list[VariableReference]:
    return _walk(collection_data.get("item", []), "")
