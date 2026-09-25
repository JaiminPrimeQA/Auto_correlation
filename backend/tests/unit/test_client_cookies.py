"""Cookies the client sets itself (a `Cookie: token=...` request header, as in
Restful-Booker) must be sent by the generated plan, with correlated values
substituted. Cookies the server set via Set-Cookie stay with the Cookie Manager."""

from __future__ import annotations

import uuid

import pytest

from app.core.config import Settings
from app.domain.enums import Confidence, RuleOrigin, RuleState
from app.domain.models import CorrelationRule
from app.services import analysis_service, jmeter_runner
from app.services.jmx_builder import BuildOptions, JmxBuilder
from tests.fixtures import builders as b


def _execs(base: str, tok: str, sid: str) -> list[dict]:
    return [
        b.execution(
            "Login", "POST", f"{base}/login", req_body=b.raw_json_body({"u": "a"}),
            resp_headers=[
                {"key": "Content-Type", "value": "application/json"},
                {"key": "Set-Cookie", "value": f"sid={sid}; Path=/"},
            ],
            resp_body={"token": tok}, position=0,
        ),
        b.execution(
            "Update", "PUT", f"{base}/update",
            req_headers=[b.header("Cookie", f"token={tok}; sid={sid}")],
            req_body=b.raw_json_body({"x": 1}), resp_body={"ok": True}, position=1,
        ),
    ]


def _build(base: str):
    a = analysis_service.build_analysis(
        [("a.json", b.to_bytes(b.report("Demo", _execs(base, "tokAAA111222", "sidAAA111222")))),
         ("b.json", b.to_bytes(b.report("Demo", _execs(base, "tokBBB333444", "sidBBB333444"))))],
        Settings(),
    )
    for c in a.candidates:
        if c.confidence == Confidence.HIGH:
            a.rules[uuid.uuid4().hex] = CorrelationRule(
                id=uuid.uuid4().hex, variable_name=c.variable_name, origin=RuleOrigin.AUTOMATIC,
                state=RuleState.ENABLED, producer=c.producer, consumers=list(c.consumers),
                extractor_method=c.extractor_method, extractor_expression=c.extractor_expression,
                confidence=c.confidence,
            )
    return a, JmxBuilder(BuildOptions()).build(a.baseline_run, list(a.rules.values()))


def test_client_set_cookie_consumer_is_added_to_the_cookie_manager():
    a, res = _build("https://api.example.com")
    assert any(r.variable_name == "token" for r in a.rules.values())
    assert res.unmaterialized == []
    assert 'testname="Set client cookies (token)"' in res.xml
    assert "[['token', vars.get('token')]]" in res.xml.replace("&apos;", "'")


def test_groovy_value_quotes_literals_and_reads_variables():
    from app.services.jmx_builder import _groovy_value

    assert _groovy_value("a${token}b") == "'a' + vars.get('token') + 'b'"
    assert _groovy_value("it's") == r"'it\'s'"
    assert _groovy_value("") == "''"


def test_server_set_cookie_is_left_to_the_cookie_manager():
    _a, res = _build("https://api.example.com")
    assert "sid=" not in res.xml  # the Cookie Manager replays it from Set-Cookie


@pytest.mark.slow
@pytest.mark.skipif(not jmeter_runner.jmeter_available(Settings()), reason="JMeter not available")
def test_real_jmeter_sends_client_and_server_cookies_together():
    import json
    import threading
    from http.cookies import SimpleCookie
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, obj, extra=()):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self._send(200, {"token": "liveTOKEN98765"}, [("Set-Cookie", "sid=liveSID55555; Path=/")])

        def do_PUT(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            jar = SimpleCookie(self.headers.get("Cookie") or "")
            ok = jar.get("token") and jar["token"].value == "liveTOKEN98765" \
                and jar.get("sid") and jar["sid"].value == "liveSID55555"
            self._send(200 if ok else 403, {"ok": bool(ok)})

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        _a, res = _build(f"http://127.0.0.1:{srv.server_address[1]}")
        rep = jmeter_runner.run_plan(res.xml, correlation_variables=res.variables,
                                     required_properties=res.required_properties, property_values={},
                                     settings=Settings())
        assert rep.status == "validated", rep.reasons
        assert rep.samplers_total == 2 and rep.samplers_failed == 0
    finally:
        srv.shutdown()
