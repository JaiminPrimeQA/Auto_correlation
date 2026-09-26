"""Capture two real Newman runs, export through the app, and replay in JMeter."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from webhook_demo import Handler, WebhookServer, collection

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "samples")
    args = parser.parse_args()
    samples = args.output_dir.resolve()
    samples.mkdir(parents=True, exist_ok=True)
    reports = [samples / "webhook-baseline.json", samples / "webhook-comparison.json"]
    source = samples / "webhook-demo.postman_collection.json"
    source.write_text(json.dumps(collection(args.port), indent=2), encoding="utf-8")
    server = WebhookServer(("127.0.0.1", args.port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        newman = shutil.which("newman.cmd") or shutil.which("newman")
        if not newman:
            raise RuntimeError("Install Newman before running this verification")
        for path in reports:
            subprocess.run([newman, "run", str(source), "--reporters", "json", "--reporter-json-export", str(path)],
                           check=True, capture_output=True, text=True, timeout=60)
            capture = json.loads(path.read_text(encoding="utf-8"))
            assert not capture["run"]["failures"]
            assert [e["response"]["code"] for e in capture["run"]["executions"]] == [200] * 7
            print(f"{path.name}: seven HTTP 200 responses, zero Newman failures", flush=True)
        client = TestClient(create_app())
        uploaded = client.post("/api/v1/analyses", files=[("files", (p.name, p.read_bytes(), "application/json")) for p in reports])
        assert uploaded.status_code == 201, uploaded.text
        aid = uploaded.json()["analysis_id"]
        automatic = client.post(f"/api/v1/analyses/{aid}/auto-correlate").json()
        assert automatic["total"] == 3, automatic
        assert client.post(f"/api/v1/analyses/{aid}/auto-correlate").json()["idempotent"]
        graph = client.get(f"/api/v1/analyses/{aid}/graph").json()
        assert graph["stats"]["edges"] == 7, graph["stats"]
        generated = client.post(f"/api/v1/analyses/{aid}/generate", json={})
        assert generated.status_code == 200, generated.text
        (samples / "webhook-correlated.jmx").write_text(client.get(f"/api/v1/analyses/{aid}/download/jmx").text, encoding="utf-8")
        (samples / "webhook-manifest.json").write_text(json.dumps(generated.json()["manifest"], indent=2), encoding="utf-8")
        validated = client.post(f"/api/v1/analyses/{aid}/validate", json={})
        assert validated.status_code == 200, validated.text
        result = validated.json()
        (samples / "webhook-validation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        report = result.get("report", result)
        assert report["status"] == "validated", result
        assert report["samplers_total"] == 7 and report["samplers_failed"] == 0, result
        print("PASS: three extractors, seven graph edges, JMeter 5.6.3 replay passed all seven requests with fresh IDs", flush=True)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
