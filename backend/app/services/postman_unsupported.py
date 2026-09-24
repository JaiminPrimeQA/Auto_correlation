"""Detect Postman collection features this product cannot execute unattended.

Unsupported cases must be reported, never silently skipped (spec §10).
"""

from __future__ import annotations

from ..domain.enums import UnsupportedFeatureKind
from ..domain.postman_models import UnsupportedFeature

_INTERACTIVE_AUTH_TYPES = {"oauth1", "ntlm", "digest"}
_INTERACTIVE_OAUTH2_GRANTS = {"authorization_code_with_pkce", "implicit"}


def _auth_findings(auth: dict, location: str) -> list[UnsupportedFeature]:
    findings: list[UnsupportedFeature] = []
    auth_type = auth.get("type")
    if auth_type in _INTERACTIVE_AUTH_TYPES:
        findings.append(UnsupportedFeature(
            kind=UnsupportedFeatureKind.INTERACTIVE_AUTH,
            detail=f"Authentication type '{auth_type}' requires an interactive handshake that cannot run unattended.",
            location=location,
        ))
    elif auth_type == "oauth2":
        params = auth.get("oauth2", [])
        grant = next((p.get("value") for p in params if isinstance(p, dict) and p.get("key") == "grantType"), None)
        if grant in _INTERACTIVE_OAUTH2_GRANTS:
            findings.append(UnsupportedFeature(
                kind=UnsupportedFeatureKind.INTERACTIVE_AUTH,
                detail=f"OAuth2 grant type '{grant}' requires a browser redirect that cannot run unattended.",
                location=location,
            ))
    return findings


def _script_findings(item: dict, location: str) -> list[UnsupportedFeature]:
    findings: list[UnsupportedFeature] = []
    for event in item.get("event", []) or []:
        if not isinstance(event, dict):
            continue
        script = event.get("script", {})
        exec_lines = script.get("exec", []) if isinstance(script, dict) else []
        text = "\n".join(exec_lines) if isinstance(exec_lines, list) else str(exec_lines)
        if "pm.sendRequest" in text:
            findings.append(UnsupportedFeature(
                kind=UnsupportedFeatureKind.DYNAMIC_REQUEST_CONSTRUCTION,
                detail="A pre-request or test script issues its own HTTP calls (pm.sendRequest), "
                       "which cannot be fully discovered before execution.",
                location=location,
            ))
    return findings


def _url_scheme(request: dict) -> str:
    url = request.get("url")
    raw_url = url if isinstance(url, str) else (url.get("raw") if isinstance(url, dict) else None)
    if not isinstance(raw_url, str) or "://" not in raw_url:
        return ""
    return raw_url.split("://", 1)[0].split("{{")[0].lower()


def _walk(items: list, path: str) -> list[UnsupportedFeature]:
    findings: list[UnsupportedFeature] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        name = it.get("name", "unnamed")
        here = f"{path} > {name}" if path else name
        if isinstance(it.get("item"), list):
            findings.extend(_script_findings(it, here))
            folder_auth = it.get("auth")
            if isinstance(folder_auth, dict):
                findings.extend(_auth_findings(folder_auth, here))
            findings.extend(_walk(it["item"], here))
            continue
        findings.extend(_script_findings(it, here))
        request = it.get("request")
        if not isinstance(request, dict):
            continue
        auth = request.get("auth")
        if isinstance(auth, dict):
            findings.extend(_auth_findings(auth, here))
        body = request.get("body", {})
        if isinstance(body, dict) and body.get("mode") == "formdata":
            for param in body.get("formdata", []) or []:
                if isinstance(param, dict) and param.get("type") == "file":
                    findings.append(UnsupportedFeature(
                        kind=UnsupportedFeatureKind.LOCAL_DATA_FILE,
                        detail=f"Form field '{param.get('key', '?')}' uploads a local file, "
                               "which is not available to the execution worker.",
                        location=here,
                    ))
        scheme = _url_scheme(request)
        if scheme and scheme not in ("http", "https"):
            findings.append(UnsupportedFeature(
                kind=UnsupportedFeatureKind.NON_HTTP_PROTOCOL,
                detail=f"Request uses unsupported protocol '{scheme}'; only HTTP(S) is supported.",
                location=here,
            ))
    return findings


def detect_unsupported_features(collection_data: dict, environment_data: dict | None = None) -> list[UnsupportedFeature]:
    findings = _walk(collection_data.get("item", []), "")
    findings.extend(_script_findings(collection_data, "collection"))
    collection_auth = collection_data.get("auth")
    if isinstance(collection_auth, dict):
        findings.extend(_auth_findings(collection_auth, "collection"))
    for source_name, data in (("collection", collection_data), ("environment", environment_data or {})):
        if isinstance(data, dict) and ("clientCertificates" in data or "certificate" in data):
            findings.append(UnsupportedFeature(
                kind=UnsupportedFeatureKind.CLIENT_CERTIFICATE,
                detail="Client-certificate authentication is referenced but not supported by the execution worker.",
                location=source_name,
            ))
    return findings
