"""End-to-end JMeter compatibility smoke test.

Builds a correlated JMX for a login->profile flow that targets the local mock
API, runs it with JMeter in non-GUI mode, and asserts that the /me sampler
returned HTTP 200 -- which can only happen if the login token was extracted and
propagated into the Authorization header.

Usage:
    python scripts/smoke_test.py --jmeter /path/to/apache-jmeter-5.6.3/bin/jmeter

Requires the backend package importable (run from repo root with backend on
PYTHONPATH, or inside the backend venv with `pip install -e` equivalent).
"""

from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import Settings  # noqa: E402
from app.domain.enums import RuleOrigin, RuleState  # noqa: E402
from app.domain.models import CorrelationRule  # noqa: E402
from app.services.correlation_engine import CorrelationEngine  # noqa: E402
from app.services.jmx_builder import BuildOptions, JmxBuilder  # noqa: E402
from app.services.jmx_validator import validate_jmx  # noqa: E402
from app.services.normalizer import normalize_run  # noqa: E402
from app.services.run_aligner import align_runs  # noqa: E402


def _buffer(text: str) -> dict:
    return {"type": "Buffer", "data": list(text.encode())}


def _report(run: str, port: int) -> dict:
    import json

    token = f"tok_{run}_{uuid.uuid4().hex}"
    host = ["127", "0", "0", "1"]
    return {
        "collection": {"info": {"name": "Smoke"}},
        "run": {"id": f"run-{run}", "executions": [
            {"cursor": {"position": 0}, "item": {"name": "Login"},
             "request": {"method": "POST",
                         "header": [{"key": "Content-Type", "value": "application/json"}],
                         "url": {"raw": f"http://127.0.0.1:{port}/auth/login", "protocol": "http",
                                 "host": host, "port": str(port), "path": ["auth", "login"], "query": []},
                         "body": {"mode": "raw", "raw": json.dumps({"u": "a"}),
                                  "options": {"raw": {"language": "json"}}}},
             "response": {"status": "OK", "code": 200,
                          "header": [{"key": "Content-Type", "value": "application/json"}],
                          "stream": _buffer(json.dumps({"token": token})), "responseTime": 10}},
            {"cursor": {"position": 1}, "item": {"name": "Get Profile"},
             "request": {"method": "GET",
                         "header": [{"key": "Authorization", "value": f"Bearer {token}"}],
                         "url": {"raw": f"http://127.0.0.1:{port}/me", "protocol": "http",
                                 "host": host, "port": str(port), "path": ["me"], "query": []}},
             "response": {"status": "OK", "code": 200,
                          "header": [{"key": "Content-Type", "value": "application/json"}],
                          "stream": _buffer(json.dumps({"id": 1})), "responseTime": 10}},
        ]},
    }


def build_plan(port: int, out: Path) -> None:
    s = Settings()
    base = normalize_run(_report("baseline", port), filename="b.json", settings=s)
    comp = normalize_run(_report("comparison", port), filename="c.json", settings=s)
    cands = CorrelationEngine(s).analyze_two_run(base, comp, align_runs(base, comp))
    assert cands, "expected a correlation candidate"
    rules = [CorrelationRule(
        id=uuid.uuid4().hex, variable_name=c.variable_name, origin=RuleOrigin.AUTOMATIC,
        state=RuleState.ENABLED, producer=c.producer, consumers=list(c.consumers),
        extractor_method=c.extractor_method, extractor_expression=c.extractor_expression,
    ) for c in cands]
    # Do not parameterise host so the plan targets the mock directly.
    result = JmxBuilder(BuildOptions(parameterize_host=False, include_static_secrets=True)).build(base, rules)
    v = validate_jmx(result.xml)
    assert v.ok, f"JMX invalid: {v.errors}"
    out.write_text(result.xml, encoding="utf-8")
    print(f"wrote {out} (vars={result.variables}, replaced={result.replaced_consumers})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jmeter", required=True, help="path to jmeter[.bat] launcher")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()

    # On Windows the runnable launcher is jmeter.bat (the extensionless file is a
    # POSIX shell script and is not a valid Win32 executable). Prefer .bat there.
    jmeter = args.jmeter
    if os.name == "nt" and not jmeter.lower().endswith(".bat") and Path(jmeter + ".bat").exists():
        jmeter = jmeter + ".bat"

    workdir = ROOT / ".smoke"
    workdir.mkdir(exist_ok=True)
    plan = workdir / "plan.jmx"
    jtl = workdir / "results.jtl"
    if jtl.exists():
        jtl.unlink()

    mock = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "mock_api.py"), str(args.port)])
    try:
        time.sleep(1.5)
        build_plan(args.port, plan)
        cmd = [jmeter, "-n", "-t", str(plan), "-l", str(jtl), "-j", str(workdir / "jmeter.log")]
        print("running:", " ".join(cmd))
        subprocess.run(cmd, check=True)
    finally:
        mock.terminate()

    # Parse the JTL and assert the /me sampler returned 200.
    rows = list(csv.DictReader(jtl.open()))
    by_label = {r["label"]: r for r in rows}
    print("samplers:", {k: v["responseCode"] for k, v in by_label.items()})
    me = next((r for r in rows if "Profile" in r["label"]), None)
    if me is None:
        print("FAIL: /me sampler not found in results")
        return 1
    if me["responseCode"] != "200":
        print(f"FAIL: /me returned {me['responseCode']} — token was not propagated")
        return 1
    print("PASS: token extracted from /auth/login propagated into /me (HTTP 200)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
