"""Secret fields in request BODIES (login passwords, client secrets, API keys)
must never be embedded in the plan: like secret headers they become
${__P(name,)} JMeter properties supplied at run time."""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.services import jmeter_runner
from app.services.jmx_builder import BuildOptions, JmxBuilder
from app.services.normalizer import normalize_run
from tests.fixtures import builders as b

_PASSWORD = "Sup3r-Secret-Pa55"


def _build(executions, **options):
    run = normalize_run(b.report("Demo", executions), filename="d.json", settings=Settings())
    return JmxBuilder(BuildOptions(**options)).build(run, [])


def _login(url="https://api.test/login", body=None):
    return b.execution(
        "Login", "POST", url,
        req_headers=[b.header("Content-Type", "application/json")],
        req_body=b.raw_json_body(body or {
            "username": "admin", "password": _PASSWORD,
            "client": {"client_secret": "cs-998877", "api_key": "ak-112233"},
            "tokenType": "Bearer",
        }),
        resp_body={"ok": True},
    )


def test_json_body_secrets_become_properties():
    res = _build([_login()])
    assert _PASSWORD not in res.xml
    assert "cs-998877" not in res.xml and "ak-112233" not in res.xml
    assert '"password": "${__P(password,)}"' in res.xml
    assert '"client_secret": "${__P(client_secret,)}"' in res.xml
    assert '"api_key": "${__P(api_key,)}"' in res.xml
    assert {"password", "client_secret", "api_key"} <= set(res.required_properties)


def test_non_secret_body_fields_stay_literal():
    res = _build([_login()])
    assert '"username": "admin"' in res.xml
    assert '"tokenType": "Bearer"' in res.xml  # not a credential


def test_form_body_password_becomes_a_property():
    ex = b.execution(
        "Login", "POST", "https://api.test/login",
        req_body={"mode": "urlencoded", "urlencoded": [
            {"key": "username", "value": "admin"}, {"key": "password", "value": _PASSWORD},
        ]},
        resp_body={"ok": True},
    )
    res = _build([ex])
    assert _PASSWORD not in res.xml
    assert "${__P(password,)}" in res.xml
    assert "password" in res.required_properties


def test_embedding_static_secrets_is_still_an_explicit_opt_in():
    res = _build([_login()], include_static_secrets=True)
    assert _PASSWORD in res.xml


@pytest.mark.slow
@pytest.mark.skipif(not jmeter_runner.jmeter_available(Settings()), reason="JMeter not available")
def test_real_jmeter_sends_the_password_supplied_at_run_time():
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            ok = body.get("password") == "run-time-pw"
            self.send_response(200 if ok else 401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/login"
        res = _build([_login(url, {"username": "admin", "password": _PASSWORD})])
        rep = jmeter_runner.run_plan(res.xml, correlation_variables=res.variables,
                                     required_properties=res.required_properties,
                                     property_values={"password": "run-time-pw"}, settings=Settings())
        assert rep.status == "validated", rep.reasons
    finally:
        srv.shutdown()
