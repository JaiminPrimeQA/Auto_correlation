"""Real-Docker Newman tests. Opt-in: B11_RUN_DOCKER_TESTS=1, a running Docker
daemon, the image built (docker build -t baseline11/newman:6.2.2 docker/newman),
and internet access to postman-echo.com."""

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.domain.execution_job import RunInput
from app.services.docker_newman_runner import DockerNewmanRunner
from app.services.postman_domain_extractor import extract_target_domains

pytestmark = pytest.mark.docker

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "newman" / "echo_collection.json"


def _docker_ready() -> bool:
    if os.environ.get("B11_RUN_DOCKER_TESTS") != "1":
        return False
    settings = Settings()
    try:
        info = subprocess.run([settings.docker_binary, "info"], capture_output=True, timeout=30)
        image = subprocess.run([settings.docker_binary, "image", "inspect", settings.newman_docker_image],
                               capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return info.returncode == 0 and image.returncode == 0


if not _docker_ready():
    pytest.skip("Docker Newman tests are opt-in (B11_RUN_DOCKER_TESTS=1, daemon + image required)",
                allow_module_level=True)


def _collection() -> dict:
    return json.loads(_FIXTURE.read_text("utf-8"))


def _never() -> bool:
    return False


def test_image_runs_the_pinned_newman_version():
    settings = Settings()
    result = subprocess.run([settings.docker_binary, "run", "--rm", settings.newman_docker_image, "--version"],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0
    assert result.stdout.strip() == settings.newman_version


def _run_once() -> dict:
    settings = Settings()
    collection = _collection()
    pins = extract_target_domains(collection, environment_values={}, settings=settings).pinned_addresses
    assert "postman-echo.com" in pins
    outcome = DockerNewmanRunner(settings).run(
        RunInput(collection_data=collection, environment_data=None, supplied_values={}, folder_id=None,
                 timeout_seconds=settings.job_run_timeout_seconds, host_pins=pins),
        should_cancel=_never,
    )
    assert outcome.success is True, outcome.error_code
    return json.loads(outcome.report_bytes or b"{}")


def test_runner_produces_a_parseable_report_with_every_request():
    report = _run_once()
    # Newman's JSON reporter also records the DNS probe's failed pm.sendRequest
    # as an extra execution of that item, so compare names, not the count.
    names = [e["item"]["name"] for e in report["run"]["executions"]]
    assert set(names) == {"Create session", "Use session", "DNS probe"}
    assert all(e["response"]["code"] == 200 for e in report["run"]["executions"])


def test_unvalidated_hostnames_cannot_be_resolved_inside_the_container():
    report = _run_once()
    values = {v["key"]: v.get("value") for v in (report.get("environment") or {}).get("values", [])}
    assert values.get("dns_probe") == "blocked"


def test_full_job_reaches_ready_with_a_correlation_candidate():
    from app.api.deps import get_job_dispatcher, get_newman_runner
    from app.main import create_app
    from app.services.job_dispatcher import InlineJobDispatcher

    app = create_app()
    app.dependency_overrides[get_newman_runner] = lambda: DockerNewmanRunner(Settings())
    app.dependency_overrides[get_job_dispatcher] = InlineJobDispatcher
    client = TestClient(app)
    created = client.post(
        "/api/v1/execution-jobs",
        data={"confirm": "true", "supplied_values_json": "{}"},
        files={"collection": ("echo.json", _FIXTURE.read_bytes(), "application/json")},
    )
    assert created.status_code == 202, created.text
    job_id = created.json()["job_id"]
    status = created.json()
    deadline = time.monotonic() + 15 * 60
    while status["state"] not in ("ready", "failed", "cancelled") and time.monotonic() < deadline:
        time.sleep(1)
        status = client.get(f"/api/v1/execution-jobs/{job_id}").json()
    assert status["state"] == "ready", status
    assert status["stage_history"] == ["validating", "running_baseline", "running_comparison", "analyzing", "ready"]

    analysis = client.get(f"/api/v1/analyses/{status['analysis_id']}").json()
    assert analysis["mode"] == "two_run"
    assert analysis["candidate_count"] >= 1
