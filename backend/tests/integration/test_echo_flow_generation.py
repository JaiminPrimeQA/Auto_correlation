"""Regression (found by the Phase 6 browser E2E): with an echo-style API, the
responses repeat the client's own Host/User-Agent, auto-correlation used to
target those headers, and JMX generation then failed its structural check."""

import json

from fastapi.testclient import TestClient

from app.main import create_app
from tests.fixtures import builders as b

_UA = "PostmanRuntime/7.39.1"


def _report(session: str) -> bytes:
    return json.dumps(b.report("Echo", [
        b.execution("Open", "GET", f"https://echo.test/get?session={session}",
                    req_headers=[b.header("Host", "echo.test"), b.header("User-Agent", _UA)],
                    resp_body={"args": {"session": session}, "headers": {"host": "echo.test", "user-agent": _UA}},
                    position=0),
        b.execution("Use", "GET", "https://echo.test/headers",
                    req_headers=[b.header("Host", "echo.test"), b.header("User-Agent", _UA),
                                 b.header("X-Session", session)],
                    resp_body={"headers": {"host": "echo.test", "user-agent": _UA, "x-session": session}},
                    position=1),
    ])).encode()


def test_auto_correlate_then_generate_succeeds_for_an_echo_api():
    client = TestClient(create_app())
    created = client.post("/api/v1/analyses", files=[
        ("files", ("baseline.json", _report("SESS-4f2a9c71aa"), "application/json")),
        ("files", ("comparison.json", _report("SESS-9e8d7c6b55"), "application/json")),
    ])
    assert created.status_code == 201, created.text
    analysis_id = created.json()["analysis_id"]

    auto = client.post(f"/api/v1/analyses/{analysis_id}/auto-correlate")
    assert auto.status_code == 200, auto.text
    assert auto.json()["total"] >= 1

    generated = client.post(f"/api/v1/analyses/{analysis_id}/generate", json={})
    assert generated.status_code == 200, generated.text
    jmx = client.get(f"/api/v1/analyses/{analysis_id}/download/jmx").text
    assert "${session}" in jmx or "session" in jmx
