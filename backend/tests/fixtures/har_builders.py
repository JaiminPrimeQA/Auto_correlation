"""Small made-up HAR recordings. Real recordings hold real credentials, so they
never belong in the test suite."""

from __future__ import annotations

import json

APP = "https://app.example.com"
API = "https://api.example.net"
JSON_HEADERS = [{"name": "content-type", "value": "application/json"}]


def entry(method, url, *, status=200, mime="application/json", resource=None, headers=None, body=None,
          post_mime="application/json", response=None, redirect="", encoding=None):
    request = {"method": method, "url": url, "headers": headers or []}
    if body is not None:
        request["postData"] = {"mimeType": post_mime, "text": body}
    content = {"mimeType": mime, "size": len(response or "")}
    if response is not None:
        content["text"] = response
    if encoding:
        content["encoding"] = encoding
    result = {
        "request": request,
        "response": {"status": status, "statusText": "", "headers": [], "cookies": [], "content": content,
                     "redirectURL": redirect},
        "time": 12.5,
    }
    if resource:
        result["_resourceType"] = resource
    return result


def har(entries, pages=(f"{APP}/",)) -> bytes:
    return json.dumps({"log": {
        "version": "1.2",
        "pages": [{"id": f"page_{i}", "title": title} for i, title in enumerate(pages)],
        "entries": entries,
    }}).encode()


def login_flow(token="tok-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", password="s3cret-pass") -> list[dict]:
    """Page, assets, trackers, a failed and a good login, then an API call."""
    good = json.dumps({"data": {"tokenGuid": token, "isLoginSuccessful": True}, "isSuccess": True})
    bad = json.dumps({"data": {"tokenGuid": None, "isLoginSuccessful": False}, "isSuccess": True})
    return [
        entry("GET", f"{APP}/login", mime="text/html", resource="document", response="<html></html>"),
        entry("GET", f"{APP}/main.js", mime="application/javascript", resource="script", response="x"),
        entry("GET", f"{APP}/site.css", mime="text/css", resource="stylesheet", response="x"),
        entry("GET", f"{APP}/logo.png", mime="image/png", resource="image", response="x"),
        entry("GET", f"{APP}/assets/i18n/lang.json", resource="fetch", response="{}"),
        entry("OPTIONS", f"{API}/api/auth/login", status=204, mime="x-unknown", resource="preflight"),
        entry("POST", "https://bam.nr-data.net/events/1", mime="image/gif", resource="ping", body="x", post_mime="text/plain"),
        entry("POST", f"{APP}/cdn-cgi/rum", status=204, mime="text/plain", resource="xhr", body="{}"),
        entry("GET", "https://chat.vendor.io/settings", resource="xhr", response='{"on": true}'),
        entry("GET", f"{API}/api/auth/get-logo", mime="image/png", resource="fetch", response="x"),
        entry("POST", f"{API}/api/auth/login", resource="fetch", headers=JSON_HEADERS, response=bad,
              body=json.dumps({"Username": "tester", "Code": "wrong-pass"})),
        entry("POST", f"{API}/api/auth/login", resource="fetch", headers=JSON_HEADERS, response=good,
              body=json.dumps({"Username": "tester", "Code": password})),
        entry("GET", f"{API}/api/users/me", resource="fetch", response='{"data": {"name": "T"}}',
              headers=[{"name": ":authority", "value": "api.example.net"}, {"name": "sec-fetch-mode", "value": "cors"},
                       {"name": "x-tokenguid", "value": token}]),
    ]
