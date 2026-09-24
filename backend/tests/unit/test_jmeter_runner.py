"""JMeter validation: JTL/log parsing + status decision (no JMeter needed),
plus a real end-to-end execution when JMeter is available.

'Validated' is only ever set after a real successful JMeter run.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.core.config import Settings
from app.domain.models import JMeterValidationReport
from app.services import jmeter_runner
from app.services.jmeter_runner import RunOutcome, _build_report

_HEADER = "timeStamp,elapsed,label,responseCode,responseMessage,success,failureMessage,token\n"


def _jtl(tmp_path: Path, rows: list[str]) -> Path:
    p = tmp_path / "result.jtl"
    p.write_text(_HEADER + "".join(r + "\n" for r in rows), encoding="utf-8")
    return p


def _report() -> JMeterValidationReport:
    return JMeterValidationReport(correlation_variables=["token"])


def test_validated_when_all_pass_and_variable_extracted(tmp_path):
    jtl = _jtl(tmp_path, [
        "1,10,01 Login,200,OK,true,,live-abc-123",
        "2,10,02 GetData,200,OK,true,,live-abc-123",
    ])
    outcome = RunOutcome(True, 0, False, jtl, None, "", "")
    rep = _build_report(outcome, _report(), ["token"], Settings())
    assert rep.status == "validated"
    assert rep.samplers_success == 2 and rep.samplers_failed == 0
    assert rep.variables_extracted == ["token"] and rep.variables_missing == []


def test_validation_failed_on_sampler_errors(tmp_path):
    jtl = _jtl(tmp_path, [
        "1,10,01 Login,200,OK,true,,live-abc-123",
        "2,10,02 GetData,500,Server Error,false,Internal error,live-abc-123",
    ])
    outcome = RunOutcome(True, 0, False, jtl, None, "", "")
    rep = _build_report(outcome, _report(), ["token"], Settings())
    assert rep.status == "validation_failed"
    assert rep.error_ratio == 0.5
    assert any("samplers failed" in r for r in rep.reasons)


def test_validation_failed_when_variable_never_extracted(tmp_path):
    jtl = _jtl(tmp_path, [
        "1,10,01 Login,200,OK,true,,",
        "2,10,02 GetData,200,OK,true,,",
    ])
    outcome = RunOutcome(True, 0, False, jtl, None, "", "")
    rep = _build_report(outcome, _report(), ["token"], Settings())
    assert rep.status == "validation_failed"
    assert rep.variables_missing == ["token"]


def test_assertion_failures_block_validation(tmp_path):
    jtl = _jtl(tmp_path, ["1,10,01 X,200,OK,false,Assertion failed: body,tok"])
    outcome = RunOutcome(True, 0, False, jtl, None, "", "")
    rep = _build_report(outcome, _report(), ["token"], Settings())
    assert rep.status == "validation_failed"
    assert rep.assertion_failures == 1


def test_auth_failure_names_the_missing_secret(tmp_path):
    jtl = _jtl(tmp_path, [
        "1,10,01 A,401,Unauthorized,false,,",
        "2,10,02 B,401,Unauthorized,false,,",
    ])
    outcome = RunOutcome(True, 0, False, jtl, None, "", "")
    rep = JMeterValidationReport(required_properties=["x_tokenguid"])  # none supplied
    rep = _build_report(outcome, rep, [], Settings())
    assert rep.status == "validation_failed"
    assert any("did not supply the required secret" in r and "x_tokenguid" in r for r in rep.reasons)


def test_unreachable_host_explains_that_extractors_never_ran(tmp_path):
    jtl = _jtl(tmp_path, [
        "1,10,01 Producer,Non HTTP response code: org.apache.http.conn.HttpHostConnectException,Connect failed,false,,",
        "2,10,02 Consumer,Non HTTP response code: org.apache.http.conn.HttpHostConnectException,Connect failed,false,,",
    ])
    outcome = RunOutcome(True, 0, False, jtl, None, "", "")
    rep = _build_report(outcome, _report(), ["token"], Settings())
    assert rep.status == "validation_failed"
    assert any("target API was unreachable" in r for r in rep.reasons)
    assert any("producer responses were never received" in r for r in rep.reasons)
    assert not any("never extracted at runtime" in r for r in rep.reasons)


def test_disabled_jmeter_is_faithful():
    s = Settings(jmeter_mode="disabled")
    rep = jmeter_runner.run_plan("<x/>", correlation_variables=[], required_properties=[], property_values={}, settings=s)
    assert rep.status == "validation_failed"
    assert rep.executed is False
    assert any("not available" in r for r in rep.reasons)


def test_timeout_with_truncated_multibyte_stdout_does_not_crash(tmp_path, monkeypatch):
    # On POSIX, TimeoutExpired.stdout/.stderr can genuinely be raw bytes even
    # with text=True (only Windows re-calls communicate() for text output on
    # timeout). If the process was killed mid multi-byte UTF-8 character, a
    # bare .decode() raises UnicodeDecodeError; errors="replace" must prevent
    # that crash (Minor #1).
    truncated = "café".encode()[:-1]  # truncated mid-character

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=1, output=truncated, stderr=truncated)

    monkeypatch.setattr(jmeter_runner.subprocess, "run", fake_run)
    settings = Settings(jmeter_timeout_seconds=1)
    outcome = jmeter_runner._execute(["jmeter"], "<x/>", [], {}, tmp_path, settings)
    assert outcome.timed_out is True
    assert isinstance(outcome.stdout, str)
    assert isinstance(outcome.stderr, str)


# --------------------------------------------------------------------------- #
# Real JMeter execution (skipped when JMeter is not installed)
# --------------------------------------------------------------------------- #

@pytest.mark.slow
@pytest.mark.skipif(not jmeter_runner.jmeter_available(Settings()), reason="JMeter not available")
def test_end_to_end_real_jmeter_run():
    import json
    import threading
    import uuid
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from app.domain.enums import Confidence, RuleOrigin, RuleState
    from app.domain.models import CorrelationRule
    from app.services import analysis_service
    from app.services.jmx_builder import BuildOptions, JmxBuilder
    from tests.fixtures import builders as b

    class Handler(BaseHTTPRequestHandler):
        def _send(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send({"ok": True})

        def do_POST(self):
            ln = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(ln)
            self._send({"token": "live-TOKEN-9931", "ok": True} if "login" in self.path else {"ok": True})

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    def execs(tok):
        return [
            b.execution("Login", "POST", f"{base}/login", req_body=b.raw_json_body({"u": "a"}),
                        resp_body={"token": tok, "ok": True}, position=0),
            b.execution("GetData", "GET", f"{base}/data",
                        req_headers=[b.header("Authorization", "Bearer " + tok)],
                        resp_body={"ok": True}, position=1),
        ]

    try:
        a = analysis_service.build_analysis(
            [("a.json", b.to_bytes(b.report("Demo", execs("AAA11122")))),
             ("b.json", b.to_bytes(b.report("Demo", execs("BBB33344"))))],
            Settings(),
        )
        assert any(c.variable_name == "token" for c in a.candidates)
        for c in a.candidates:
            if c.confidence == Confidence.HIGH:
                a.rules[uuid.uuid4().hex] = CorrelationRule(
                    id=uuid.uuid4().hex, variable_name=c.variable_name, origin=RuleOrigin.AUTOMATIC,
                    state=RuleState.ENABLED, producer=c.producer, consumers=list(c.consumers),
                    extractor_method=c.extractor_method, extractor_expression=c.extractor_expression,
                    confidence=c.confidence,
                )
        res = JmxBuilder(BuildOptions()).build(a.baseline_run, list(a.rules.values()))
        rep = jmeter_runner.run_plan(res.xml, correlation_variables=res.variables,
                                     required_properties=res.required_properties, property_values={}, settings=Settings())
        assert rep.status == "validated", rep.reasons
        assert rep.samplers_total == 2 and rep.samplers_failed == 0
        assert rep.variables_extracted == ["token"]
    finally:
        srv.shutdown()
