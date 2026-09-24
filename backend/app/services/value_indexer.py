"""Index scalar values from requests (sinks) and responses (sources).

Every value keeps its precise, machine-usable location so an extractor can be
generated later. Only safe, well-known transformations are considered (raw,
URL encode/decode, JSON-escape, and the ``Bearer <value>`` wrapper). No
arbitrary code-based transformations are ever applied.
"""

from __future__ import annotations

import re
from urllib.parse import quote, unquote

from ..domain.enums import DataType, LocationType, Side
from ..domain.models import NormalizedExecution, ValueOccurrence
from ..utils.xml import safe_parse_xml

# HTML form fields: <input name="csrf" value="TOKEN"> (both attribute orders).
_HTML_NAME_VALUE = re.compile(r'name="([^"]+)"[^>]*?value="([^"]*)"', re.I)
_HTML_VALUE_NAME = re.compile(r'value="([^"]*)"[^>]*?name="([^"]+)"', re.I)


def _data_type(value: object) -> DataType:
    if value is None:
        return DataType.NULL
    if isinstance(value, bool):
        return DataType.BOOLEAN
    if isinstance(value, (int, float)):
        return DataType.NUMBER
    return DataType.STRING


def flatten_json(obj: object, base: str = "$") -> list[tuple[str, object]]:
    """Flatten JSON into (JSONPath, scalar_value) leaves."""
    out: list[tuple[str, object]] = []
    stack: list[tuple[str, object]] = [(base, obj)]
    while stack:
        path, node = stack.pop()
        if isinstance(node, dict):
            for k, v in node.items():
                child = f"{path}.{k}" if _is_ident(k) else f"{path}['{k}']"
                stack.append((child, v))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                stack.append((f"{path}[{i}]", v))
        else:
            out.append((path, node))
    return out


def _is_ident(k: str) -> bool:
    return bool(k) and (k[0].isalpha() or k[0] == "_") and all(c.isalnum() or c == "_" for c in k)


def _transforms(value: str) -> list[tuple[str, str, str | None]]:
    """Return (normalized_value, transform_label, wrapper) representations."""
    out: list[tuple[str, str, str | None]] = [(value, "raw", None)]
    enc = quote(value, safe="")
    if enc != value:
        out.append((enc, "url_encode", None))
    dec = unquote(value)
    if dec != value:
        out.append((dec, "url_decode", None))
    esc = value.replace('"', '\\"')
    if esc != value:
        out.append((esc, "json_escape", None))
    return out


def index_response_sources(execution: NormalizedExecution) -> list[ValueOccurrence]:
    """Producer candidates from a response (JSON leaves, headers, cookies, XML)."""
    resp = execution.response
    if resp is None or not resp.present:
        return []
    out: list[ValueOccurrence] = []

    # JSON body leaves
    if resp.parsed_body is not None:
        for path, value in flatten_json(resp.parsed_body):
            if _scalarish(value):
                out.append(_occ(execution, Side.RESPONSE, LocationType.JSON_BODY, path, None, value))

    # Response headers
    for h in resp.headers:
        if h.name.lower() == "set-cookie":
            name, val = _parse_set_cookie(h.value)
            if name:
                out.append(_occ(execution, Side.RESPONSE, LocationType.COOKIE, name, name, val))
        else:
            out.append(_occ(execution, Side.RESPONSE, LocationType.HEADER, h.name, h.name, h.value))

    # Explicit response cookies
    for c in resp.cookies:
        out.append(_occ(execution, Side.RESPONSE, LocationType.COOKIE, c.name, c.name, c.value))

    # XML body nodes (safe parsing)
    if resp.content_type and "xml" in resp.content_type.lower():
        root = safe_parse_xml(resp.raw_body_text)
        if root is not None:
            for xpath, value in _flatten_xml(root):
                out.append(_occ(execution, Side.RESPONSE, LocationType.XML_BODY, xpath, None, value))

    # HTML / unstructured text segments (lower confidence). Only when there is no
    # structured JSON body to prefer.
    if resp.parsed_body is None and resp.raw_body_text:
        out.extend(_text_sources(execution, resp.raw_body_text))

    return out


def _text_sources(execution: NormalizedExecution, text: str) -> list[ValueOccurrence]:
    """Extract HTML form field values as lower-confidence producer sources.

    Each source carries a regex (in ``canonical_path``) that captures the value
    given its field name, so a Regex Extractor can be attached to the producer.
    """
    out: list[ValueOccurrence] = []
    seen: set[tuple[str, str]] = set()
    for pattern, order in ((_HTML_NAME_VALUE, "nv"), (_HTML_VALUE_NAME, "vn")):
        for m in pattern.finditer(text):
            if order == "nv":
                key, value = m.group(1), m.group(2)
            else:
                value, key = m.group(1), m.group(2)
            if not value or (key, value) in seen:
                continue
            seen.add((key, value))
            expr = rf'name="{re.escape(key)}"[^>]*?value="([^"]*)"'
            out.append(_occ(execution, Side.RESPONSE, LocationType.TEXT_BODY, expr, key, value))
    return out


def index_request_sinks(execution: NormalizedExecution) -> list[ValueOccurrence]:
    """Consumer sinks from a request (path, query, headers, cookies, body, form)."""
    req = execution.request
    out: list[ValueOccurrence] = []

    for i, seg in enumerate(req.path_segments):
        out.append(_occ(execution, Side.REQUEST, LocationType.PATH, f"[{i}]", str(i), seg))
    for q in req.query:
        out.append(_occ(execution, Side.REQUEST, LocationType.QUERY, q.name, q.name, q.value))
    for h in req.headers:
        out.append(_occ(execution, Side.REQUEST, LocationType.HEADER, h.name, h.name, h.value))
    for c in req.cookies:
        out.append(_occ(execution, Side.REQUEST, LocationType.COOKIE, c.name, c.name, c.value))
    for f in req.form_data:
        out.append(_occ(execution, Side.REQUEST, LocationType.FORM, f.name, f.name, f.value))

    if req.parsed_body is not None:
        for path, value in flatten_json(req.parsed_body):
            if _scalarish(value):
                out.append(_occ(execution, Side.REQUEST, LocationType.JSON_BODY, path, None, value))
    elif req.raw_body:
        out.append(_occ(execution, Side.REQUEST, LocationType.TEXT_BODY, "$", None, req.raw_body))

    return out


def _scalarish(value: object) -> bool:
    return isinstance(value, (str, int, float)) and not isinstance(value, bool)


def _occ(
    execution: NormalizedExecution,
    side: Side,
    loc: LocationType,
    path: str,
    key: str | None,
    value: object,
) -> ValueOccurrence:
    raw = "" if value is None else str(value)
    return ValueOccurrence(
        execution_id=execution.id,
        execution_index=execution.original_index,
        side=side,
        location_type=loc,
        canonical_path=path,
        key=key,
        raw_value=raw,
        normalized_value=raw.strip(),
        data_type=_data_type(value),
    )


def _parse_set_cookie(header_value: str) -> tuple[str, str]:
    first = header_value.split(";", 1)[0]
    name, _, val = first.partition("=")
    return name.strip(), val.strip()


def _flatten_xml(root, prefix: str = "") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    tag = root.tag.split("}")[-1]
    path = f"{prefix}/{tag}"
    for attr, val in root.attrib.items():
        out.append((f"{path}/@{attr.split('}')[-1]}", val))
    text = (root.text or "").strip()
    if text:
        out.append((path, text))
    for child in list(root):
        out.extend(_flatten_xml(child, path))
    return out


def occurrence_transforms(value: str) -> list[tuple[str, str, str | None]]:
    """Expose transform variants for a value (used by the correlation engine)."""
    return _transforms(value)
