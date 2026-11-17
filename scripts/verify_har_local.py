"""Replay a converted HAR plan against localhost with fresh response values.

This checks the generated plan, not the availability/authentication of the real site.
No recorded host is contacted. Redirects are flattened to their recorded final
response; redirect behavior has separate real-JMeter integration tests.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import threading
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote_plus

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.config import Settings  # noqa: E402
from app.services.analysis_service import build_har_analysis  # noqa: E402
from app.services.auto_correlator import auto_correlate  # noqa: E402
from app.services.har_import import parse_har  # noqa: E402
from app.services.jmeter_runner import run_plan  # noqa: E402


def verify(source: Path, plan: Path) -> dict:
    settings = Settings()
    recording = parse_har(source.read_bytes(), filename=source.name, settings=settings)
    run = build_har_analysis([recording], [recording.default_selection()], settings).sequence_run
    rules = auto_correlate(run, settings)
    fresh = {r.producer.raw_value: secrets.token_hex(20) for r in rules}
    consumers = {}
    for rule in rules:
        for consumer in rule.consumers:
            consumers.setdefault(consumer.execution_index, set()).add(rule.producer.raw_value)
    seen = []
    failures = []

    class Handler(BaseHTTPRequestHandler):
        def respond(self):
            index = len(seen)
            seen.append(self.path.partition("?")[0])
            if index >= len(run.executions):
                self.send_error(500)
                return
            execution = run.executions[index]
            request_body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
            received = unquote_plus(self.path) + str(self.headers) + request_body
            expected = consumers.get(index, set())
            errors = ["stale or missing correlation" for old in expected
                      if old in received or fresh[old] not in received]
            if self.command != execution.method or self.path.partition("?")[0] != execution.request.path:
                errors.append("method/path mismatch")
            failures.extend({"request_index": index, "error": error} for error in errors)
            response = execution.response
            body = response.raw_body_text if response else ""
            for old, new in fresh.items():
                body = body.replace(old, new)
            payload = body.encode()
            self.send_response(409 if errors else 200)
            self.send_header("Content-Type", "application/json" if response and response.parsed_body is not None else "text/html")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = respond

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    tree = ET.fromstring(plan.read_text(encoding="utf-8"))
    samplers = tree.findall(".//HTTPSamplerProxy")
    assert len(samplers) == len(run.executions), "Plan does not match the default HAR selection"
    for sampler in samplers:
        for prop in sampler.findall("stringProp"):
            if prop.get("name") in ("HTTPSampler.domain", "HTTPSampler.port", "HTTPSampler.protocol"):
                prop.text = {"HTTPSampler.domain": "127.0.0.1", "HTTPSampler.port": str(server.server_port),
                             "HTTPSampler.protocol": "http"}[prop.get("name")]
    try:
        report = run_plan(ET.tostring(tree, encoding="unicode"),
                          correlation_variables=[r.variable_name for r in rules],
                          required_properties=["Code"], property_values={"Code": "local-verification-only"},
                          settings=settings)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    result = {"scope": "localhost mock; not live target validation", "samplers_expected": len(run.executions),
              "requests_received": len(seen), "request_failures": failures,
              "jmeter": report.model_dump(mode="json")}
    plan.with_suffix(".local-validation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    assert report.status == "validated" and not failures and len(seen) == len(run.executions), result
    return {"requests": len(seen), "failed": report.samplers_failed,
            "variables": [r.variable_name for r in rules], "scope": result["scope"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.input, args.plan), indent=2))
