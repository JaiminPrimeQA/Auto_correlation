"""Synthetic, non-sensitive Newman JSON report builders for tests.

These construct minimal but realistic Newman ``run.executions`` structures,
including Buffer-encoded response bodies, so the parser/normalizer/engine can be
exercised without any real credentials or captured hosts.
"""

from __future__ import annotations

import json


def buffer_body(text: str) -> dict:
    """Encode text as a Newman Buffer stream ({type: Buffer, data: [ints]})."""
    return {"type": "Buffer", "data": list(text.encode("utf-8"))}


def header(name: str, value: str) -> dict:
    return {"key": name, "value": value}


def url_object(raw: str) -> dict:
    from urllib.parse import urlsplit

    parts = urlsplit(raw)
    query = []
    if parts.query:
        for chunk in parts.query.split("&"):
            k, _, v = chunk.partition("=")
            query.append({"key": k, "value": v})
    return {
        "raw": raw,
        "protocol": parts.scheme,
        "host": parts.hostname.split(".") if parts.hostname else [],
        "port": str(parts.port) if parts.port else "",
        "path": [p for p in parts.path.split("/") if p],
        "query": query,
    }


def execution(
    name: str,
    method: str,
    url: str,
    *,
    req_headers: list[dict] | None = None,
    req_body: dict | None = None,
    resp_code: int = 200,
    resp_status: str = "OK",
    resp_headers: list[dict] | None = None,
    resp_body: object = None,
    as_buffer: bool = True,
    position: int = 0,
    assertions: list[dict] | None = None,
    request_error: dict | None = None,
) -> dict:
    request: dict = {
        "method": method,
        "header": req_headers or [],
        "url": url_object(url),
    }
    if req_body is not None:
        request["body"] = req_body

    response: dict = {
        "id": f"resp-{name}",
        "status": resp_status,
        "code": resp_code,
        "header": resp_headers or [{"key": "Content-Type", "value": "application/json"}],
        "responseTime": 42,
    }
    if resp_body is not None:
        text = resp_body if isinstance(resp_body, str) else json.dumps(resp_body)
        if as_buffer:
            response["stream"] = buffer_body(text)
        else:
            response["body"] = text
    exec_obj = {
        "cursor": {"position": position},
        "item": {"name": name},
        "request": request,
        "response": response,
        "assertions": assertions or [],
    }
    if request_error is not None:
        exec_obj["requestError"] = request_error
    return exec_obj


def failed_assertion(name: str, message: str) -> dict:
    return {"assertion": name, "error": {"message": message}}


def raw_json_body(obj: object) -> dict:
    return {
        "mode": "raw",
        "raw": json.dumps(obj),
        "options": {"raw": {"language": "json"}},
    }


def report(collection_name: str, executions: list[dict]) -> dict:
    return {
        "collection": {"info": {"name": collection_name}},
        "run": {"id": f"run-{collection_name}", "executions": executions},
    }


def to_bytes(obj: dict) -> bytes:
    return json.dumps(obj).encode("utf-8")
