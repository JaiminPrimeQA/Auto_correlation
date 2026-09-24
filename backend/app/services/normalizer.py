"""Normalise raw Newman executions into the canonical domain model.

Handles Newman's several URL / header / body shapes. Order and duplicates of
headers and query parameters are preserved (as lists of pairs), because JMeter
reconstruction must be faithful.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from ..core.config import Settings
from ..domain.enums import BodyMode
from ..domain.models import (
    Assertion,
    NormalizedExecution,
    NormalizedRequest,
    NormalizedResponse,
    NormalizedRun,
    Pair,
)
from ..utils.encoding import decode_body


def _as_pairs(items: object, key_field: str = "key", val_field: str = "value") -> list[Pair]:
    out: list[Pair] = []
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict):
                if it.get("disabled") is True:
                    continue
                k = it.get(key_field)
                v = it.get(val_field, "")
                if k is None:
                    continue
                out.append(Pair(name=str(k), value="" if v is None else str(v)))
    return out


def _host_to_str(host: object) -> str:
    if isinstance(host, list):
        return ".".join(str(h) for h in host)
    return str(host) if host is not None else ""


def _path_segments(path: object) -> list[str]:
    if isinstance(path, list):
        return [str(p) for p in path]
    if isinstance(path, str):
        return [seg for seg in path.split("/") if seg != ""]
    return []


def _normalize_url(url: object) -> tuple[str, str, int | None, str, list[str], list[Pair], str]:
    """Return (protocol, host, port, path, path_segments, query_pairs, raw_url)."""
    if isinstance(url, str):
        parts = urlsplit(url)
        protocol = parts.scheme or "https"
        host = parts.hostname or ""
        port = parts.port
        path = parts.path or "/"
        segs = _path_segments(path)
        query: list[Pair] = []
        if parts.query:
            for chunk in parts.query.split("&"):
                if not chunk:
                    continue
                k, _, v = chunk.partition("=")
                query.append(Pair(name=k, value=v))
        return protocol, host, port, path, segs, query, url

    if isinstance(url, dict):
        protocol = str(url.get("protocol") or "https")
        host = _host_to_str(url.get("host"))
        raw_port = str(url.get("port") or "")
        port = int(raw_port) if raw_port.isdigit() else None
        segs = _path_segments(url.get("path"))
        path = "/" + "/".join(segs) if segs else "/"
        query = _as_pairs(url.get("query"))
        raw = str(url.get("raw") or "")
        if not raw:
            netloc = host + (f":{port}" if port else "")
            qs = "&".join(f"{p.name}={p.value}" for p in query)
            raw = f"{protocol}://{netloc}{path}" + (f"?{qs}" if qs else "")
        return protocol, host, port, path, segs, query, raw

    return "https", "", None, "/", [], [], ""


def _body(request_obj: dict, headers: list[Pair]) -> tuple[BodyMode, str | None, object | None, list[Pair], list[dict[str, str]], str | None]:
    body = request_obj.get("body")
    content_type = next((p.value for p in headers if p.name.lower() == "content-type"), None)
    if not isinstance(body, dict):
        return BodyMode.NONE, None, None, [], [], content_type

    mode = body.get("mode")
    if mode == "raw":
        raw = body.get("raw")
        raw_text = raw if isinstance(raw, str) else (json.dumps(raw) if raw is not None else None)
        lang = ((body.get("options") or {}).get("raw") or {}).get("language", "")
        parsed = None
        bmode = BodyMode.RAW
        ct = (content_type or "").lower()
        if lang == "json" or "json" in ct or _looks_json(raw_text):
            bmode = BodyMode.JSON
            parsed = _try_json(raw_text)
        elif lang == "xml" or "xml" in ct:
            bmode = BodyMode.XML
        elif lang == "text":
            bmode = BodyMode.TEXT
        return bmode, raw_text, parsed, [], [], content_type
    if mode == "urlencoded":
        return BodyMode.URLENCODED, None, None, _as_pairs(body.get("urlencoded")), [], content_type
    if mode == "formdata":
        fields: list[Pair] = []
        files: list[dict[str, str]] = []
        for it in body.get("formdata", []) or []:
            if not isinstance(it, dict) or it.get("disabled") is True:
                continue
            if it.get("type") == "file":
                files.append({"name": str(it.get("key", "")), "src": str(it.get("src", ""))})
            else:
                fields.append(Pair(name=str(it.get("key", "")), value=str(it.get("value", ""))))
        return BodyMode.FORMDATA, None, None, fields, files, content_type
    return BodyMode.NONE, None, None, [], [], content_type


def _looks_json(text: str | None) -> bool:
    if not text:
        return False
    t = text.strip()
    return (t.startswith("{") and t.endswith("}")) or (t.startswith("[") and t.endswith("]"))


def _try_json(text: str | None) -> object | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _normalize_response(resp: object, settings: Settings) -> NormalizedResponse | None:
    if not isinstance(resp, dict):
        return None
    headers = _as_pairs(resp.get("header"))
    cookies = _as_pairs(resp.get("cookie"), key_field="key", val_field="value")
    content_type = next((p.value for p in headers if p.name.lower() == "content-type"), None)
    decoded = decode_body(resp.get("stream"), resp.get("body"), max_bytes=settings.max_buffer_length)
    parsed = _try_json(decoded.text) if decoded.text else None
    return NormalizedResponse(
        status=str(resp.get("status") or ""),
        code=int(resp.get("code") or 0),
        headers=headers,
        cookies=cookies,
        content_type=content_type,
        raw_body_text=decoded.text,
        parsed_body=parsed,
        body_size=decoded.byte_length,
        decoding_warnings=decoded.warnings,
        present=True,
    )


def _assertions(exec_obj: dict) -> list[Assertion]:
    out: list[Assertion] = []
    for a in exec_obj.get("assertions", []) or []:
        if not isinstance(a, dict):
            continue
        err = a.get("error")
        out.append(
            Assertion(
                name=str(a.get("assertion", "assertion")),
                failed=err is not None,
                error_message=str(err.get("message")) if isinstance(err, dict) else None,
            )
        )
    return out


def _item_path(exec_obj: dict) -> tuple[list[str], str]:
    item = exec_obj.get("item")
    name = ""
    if isinstance(item, dict):
        name = str(item.get("name", ""))
    # Newman does not always include folder ancestry; use item name as leaf.
    path = [name] if name else []
    return path, name


def normalize_run(data: dict, *, filename: str, settings: Settings) -> NormalizedRun:
    run = data["run"]
    executions = run["executions"]
    collection = ""
    info = (data.get("collection") or {}).get("info") if isinstance(data.get("collection"), dict) else None
    if isinstance(info, dict):
        collection = str(info.get("name", ""))

    run_id = str(run.get("id") or filename)
    normalized: list[NormalizedExecution] = []
    for idx, exec_obj in enumerate(executions):
        if not isinstance(exec_obj, dict):
            continue
        req_obj = exec_obj.get("request")
        if not isinstance(req_obj, dict):
            continue
        headers = _as_pairs(req_obj.get("header"))
        protocol, host, port, path, segs, query, raw_url = _normalize_url(req_obj.get("url"))
        bmode, raw_body, parsed_body, form, files, content_type = _body(req_obj, headers)
        method = str(req_obj.get("method", "GET")).upper()
        request = NormalizedRequest(
            method=method,
            protocol=protocol,
            host=host,
            port=port,
            path=path,
            path_segments=segs,
            raw_url=raw_url,
            query=query,
            headers=headers,
            cookies=_extract_request_cookies(headers),
            body_mode=bmode,
            raw_body=raw_body,
            parsed_body=parsed_body,
            form_data=form,
            files=files,
            content_type=content_type,
        )
        item_path, item_name = _item_path(exec_obj)
        response = _normalize_response(exec_obj.get("response"), settings)
        cursor = exec_obj.get("cursor") if isinstance(exec_obj.get("cursor"), dict) else {}
        normalized.append(
            NormalizedExecution(
                id=f"{run_id}:{idx}",
                original_index=idx,
                cursor_position=cursor.get("position") if isinstance(cursor, dict) else None,
                item_path=item_path,
                item_name=item_name or f"request-{idx}",
                method=method,
                normalized_url=raw_url,
                request=request,
                response=response,
                assertions=_assertions(exec_obj),
                request_error=_request_error(exec_obj.get("requestError")),
                started_at=None,
                duration_ms=_response_time(exec_obj.get("response")),
            )
        )

    return NormalizedRun(
        run_id=run_id,
        filename=filename,
        collection_name=collection,
        executions=normalized,
    )


def _request_error(err: object) -> str | None:
    """Normalise a Newman ``requestError`` (network failure / timeout)."""
    if err is None:
        return None
    if isinstance(err, dict):
        return str(err.get("message") or err.get("code") or "request error")
    return str(err)


def _response_time(resp: object) -> float | None:
    if not isinstance(resp, dict):
        return None
    rt = resp.get("responseTime")
    if isinstance(rt, (int, float)):
        return float(rt)
    if isinstance(rt, str) and rt.replace(".", "", 1).isdigit():
        return float(rt)
    return None


def _extract_request_cookies(headers: list[Pair]) -> list[Pair]:
    cookies: list[Pair] = []
    for h in headers:
        if h.name.lower() == "cookie":
            for chunk in h.value.split(";"):
                k, _, v = chunk.strip().partition("=")
                if k:
                    cookies.append(Pair(name=k, value=v))
    return cookies
