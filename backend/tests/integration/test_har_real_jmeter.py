"""A plan from a browser recording, run by real JMeter against a local server
that issues a fresh token on every login."""

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_settings
from app.core.config import Settings
from app.main import create_app
from app.services import jmeter_runner
from tests.fixtures.har_builders import JSON_HEADERS, entry, har

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not jmeter_runner.jmeter_available(Settings()), reason="JMeter not available"),
]
PASSWORD = "mock-password"
AGENT = {"name": "user-agent", "value": "Mozilla/5.0 Test"}


class Api(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen: list[str]
    live: set[str]
    ticket: str = ""

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path == "/ticket":
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            return self.do_POST_ticket()
        self.seen.append(self.path)
        sent = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        if sent.get("Code") != PASSWORD:  # a failed login is an HTTP 200 here
            return self._send(200, {"data": {"tokenGuid": None, "isLoginSuccessful": False}, "isSuccess": False})
        token = secrets.token_hex(16)
        self.live.add(token)
        self._send(200, {"data": {"tokenGuid": token, "isLoginSuccessful": True}, "isSuccess": True})

    def _html(self, code, body="", location=None):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(data)))
        if location:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(data)

    def do_PUT(self):
        """Each value must be today's, not the recorded one."""
        self.seen.append(self.path)
        sent = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        fresh = {sent.get("code"), self.headers.get("X-CSRF-Token")} <= self.live
        self._send(200 if fresh else 401, {"isSuccess": fresh})

    def do_GET(self):
        path = self.path.partition("?")[0]
        if path == "/start":
            code = secrets.token_hex(16)
            self.live.add(code)
            return self._html(302, location=f"/cb?code={code}")
        if path == "/cb":
            csrf = secrets.token_hex(16)
            self.live.add(csrf)
            return self._html(200, f'<meta name="csrf-token" content="{csrf}">')
        if path == "/encoded":
            ok = parse_qs(self.path.partition("?")[2]).get("ticket") == [self.ticket]
            return self._send(200 if ok else 401, {"isSuccess": ok})
        self.seen.append(self.path)
        ok = "Mozilla" in self.headers.get("User-Agent", "") and self.headers.get("x-tokenguid") in self.live
        self._send(200 if ok else 401, {"isSuccess": ok})

    def do_POST_ticket(self):
        Api.ticket = "fr/esh+" + secrets.token_hex(12) + "=="
        self._send(200, {"ticket": Api.ticket})

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    Api.seen, Api.live = [], set()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Api)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def client_for_plan() -> TestClient:
    settings = Settings(rate_limit_enabled=False, trusted_proxy_hops=0)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def plan(base: str) -> tuple[str, list[str]]:
    token = "recorded-token-value-0123456789abcdef"
    raw = har([
        entry("POST", f"{base}/api/auth/login", headers=[*JSON_HEADERS, AGENT],
              body=json.dumps({"Username": "tester", "Code": "recorded-password"}),
              response=json.dumps({"data": {"tokenGuid": token, "isLoginSuccessful": True}, "isSuccess": True})),
        entry("GET", f"{base}/api/me", response=json.dumps({"isSuccess": True}),
              headers=[AGENT, {"name": "x-tokenguid", "value": token}]),
    ], pages=(f"{base}/",))
    client = client_for_plan()
    upload = {"files": ("flow.har", raw, "application/json")}
    analysis_id = client.post("/api/v1/analyses/har", files=upload, data={"selected": "[0, 1]"}).json()["analysis_id"]
    variables = client.post(f"/api/v1/analyses/{analysis_id}/auto-correlate").json()["variables"]
    client.post(f"/api/v1/analyses/{analysis_id}/generate", json={})
    return client.get(f"/api/v1/analyses/{analysis_id}/download/jmx").text, variables


def test_redirect_code_meta_token_and_encoded_value_stay_live(server):
    recorded, csrf = "recordedcode0123456789abcdef", "recordedcsrf0123456789abcdef"
    ticket = "ab/cd+ef==1234567890"
    raw = har([
        entry("GET", f"{server}/start", status=302, mime="text/html", redirect=f"{server}/cb?code={recorded}",
              resource="document", headers=[AGENT]),
        entry("GET", f"{server}/cb?code={recorded}", mime="text/html", resource="document",
              response=f'<meta name="csrf-token" content="{csrf}">'),
        entry("PUT", f"{server}/api/exchange", body=json.dumps({"code": recorded}), response='{"isSuccess": true}',
              headers=[*JSON_HEADERS, AGENT, {"name": "X-CSRF-Token", "value": csrf}]),
        entry("POST", f"{server}/ticket", body="{}", response=json.dumps({"ticket": ticket}), headers=[AGENT]),
        entry("GET", f"{server}/encoded?ticket=ab%2Fcd%2Bef%3D%3D1234567890", response='{"isSuccess": true}',
              headers=[AGENT]),
    ], pages=(f"{server}/",))
    client = client_for_plan()
    upload = {"files": ("flow.har", raw, "application/json")}
    analysis_id = client.post("/api/v1/analyses/har", files=upload, data={"selected": "[0, 2, 3, 4]"}).json()["analysis_id"]
    variables = client.post(f"/api/v1/analyses/{analysis_id}/auto-correlate").json()["variables"]
    assert sorted(variables) == ["code", "csrf_token", "ticket"]
    client.post(f"/api/v1/analyses/{analysis_id}/generate", json={})
    xml = client.get(f"/api/v1/analyses/{analysis_id}/download/jmx").text
    report = jmeter_runner.run_plan(xml, correlation_variables=variables, required_properties=[], property_values={},
                                    settings=Settings())
    assert report.status == "validated", report.reasons
    assert report.samplers_failed == 0


def test_no_password_sends_no_login(server):
    xml, variables = plan(server)
    jmeter_runner.run_plan(xml, correlation_variables=variables, required_properties=[], property_values={},
                           settings=Settings())
    assert Api.seen == []


def run(xml, variables, password):
    return jmeter_runner.run_plan(xml, correlation_variables=variables, required_properties=["Code"],
                                  property_values={"Code": password}, settings=Settings())


def test_correct_password_runs_the_whole_flow(server):
    report = run(*plan(server), PASSWORD)
    assert report.status == "validated", report.reasons
    assert (report.samplers_total, report.samplers_failed) == (2, 0)


def test_wrong_password_fails_the_login_and_stops_the_user(server):
    report = run(*plan(server), "wrong-password")
    assert report.samplers_failed == 1
    assert Api.seen == ["/api/auth/login"]
