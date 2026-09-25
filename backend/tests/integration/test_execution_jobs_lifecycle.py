# backend/tests/integration/test_execution_jobs_lifecycle.py
"""HTTP-level integration tests for the full execution-job lifecycle
(Tasks 10-12): create -> poll -> analysis, cancellation, idempotency,
ownership, and the pre-existing Phase 1 endpoints staying intact.

Amendment A7 notes (see .superpowers/sdd/2026-09-24-execution-job-model/amendments.md):
Starlette's TestClient runs BackgroundTasks to completion before `post()`
returns, so a freshly created job is already terminal by the time the test
next inspects it - polling is still used for the happy-path test for
robustness, but most tests assert state immediately after `post()`.
`get_job_store()` is an `lru_cache` singleton shared across the whole test
session, so tests never assume it starts empty. The TestClient's client host
(owner key) is `"testclient"`.
"""

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_job_dispatcher, get_job_store, get_newman_runner
from app.api.v1.execution_jobs import _coerce_supplied_values
from app.domain.execution_job import ExecutionJob, ExecutionJobState
from app.main import create_app
from app.services.fake_newman_runner import canned_fake_runner
from app.services.job_dispatcher import InlineJobDispatcher
from tests.fixtures.postman_builders import pm_collection, pm_environment, pm_request


@pytest.fixture
def client():
    # The runner is disabled by default (spec §4); the lifecycle tests opt in
    # to the deterministic canned fake explicitly, per request.
    app = create_app()
    app.dependency_overrides[get_newman_runner] = canned_fake_runner
    app.dependency_overrides[get_job_dispatcher] = InlineJobDispatcher
    return TestClient(app)


@pytest.fixture
def default_client():
    """A client with NO runner override - i.e. the shipped default setting."""
    return TestClient(create_app())


def _collection():
    return pm_collection("Smoke", [pm_request("Ping", "GET", "https://93.184.216.34/ping")])


def _create(client, **form_overrides):
    form = {"confirm": "true", "supplied_values_json": "{}"}
    form.update(form_overrides)
    return client.post(
        "/api/v1/execution-jobs",
        data=form,
        files={"collection": ("c.json", json.dumps(_collection()).encode(), "application/json")},
    )


def test_create_job_returns_202_and_reaches_ready(client):
    resp = _create(client)
    assert resp.status_code == 202
    body = resp.json()
    assert body["state"] == "queued"
    job_id = body["job_id"]

    status = body
    for _ in range(20):
        status = client.get(f"/api/v1/execution-jobs/{job_id}").json()
        if status["state"] in ("ready", "failed"):
            break
        time.sleep(0.05)
    assert status["state"] == "ready"
    assert status["analysis_id"] is not None

    analysis_resp = client.get(f"/api/v1/analyses/{status['analysis_id']}")
    assert analysis_resp.status_code == 200


def test_create_job_with_default_runner_setting_is_503_and_creates_no_job(default_client):
    resp = default_client.post(
        "/api/v1/execution-jobs",
        data={"confirm": "true", "supplied_values_json": "{}"},
        files={"collection": ("c.json", json.dumps(_collection()).encode(), "application/json")},
        headers={"Idempotency-Key": "runner-disabled-probe"},
    )
    assert resp.status_code == 503
    body = resp.json()
    assert body["code"] == "runner_unavailable"
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert get_job_store().find_by_idempotency_key("testclient", "runner-disabled-probe", window_seconds=600) is None


def test_endpoint_hands_the_runner_only_the_supplied_values():
    runner = canned_fake_runner()
    app = create_app()
    app.dependency_overrides[get_newman_runner] = lambda: runner
    app.dependency_overrides[get_job_dispatcher] = InlineJobDispatcher
    collection = pm_collection(
        "Threaded",
        [pm_request("Ping", "GET", "https://{{host}}/ping?t={{token}}&e={{env_var}}")],
        variables=[{"key": "host", "value": "93.184.216.34"}],
    )
    environment = pm_environment("Env", {"env_var": "from-env"})
    resp = TestClient(app).post(
        "/api/v1/execution-jobs",
        data={"confirm": "true", "supplied_values_json": json.dumps({"token": "supplied-tok"})},
        files={
            "collection": ("c.json", json.dumps(collection).encode(), "application/json"),
            "environment": ("e.json", json.dumps(environment).encode(), "application/json"),
        },
    )
    assert resp.status_code == 202
    assert len(runner.calls) == 2
    for call in runner.calls:
        assert call.supplied_values == {"token": "supplied-tok"}
        assert call.environment_data is not None and call.environment_data["name"] == "Env"
    assert runner.calls[0].collection_data is not runner.calls[1].collection_data


def test_create_job_rejects_without_confirmation(client):
    resp = _create(client, confirm="false")
    assert resp.status_code == 422


def test_get_unknown_job_is_404(client):
    resp = client.get("/api/v1/execution-jobs/does-not-exist")
    assert resp.status_code == 404


def test_delete_completed_job_removes_it(client):
    resp = _create(client)
    job_id = resp.json()["job_id"]

    delete_resp = client.delete(f"/api/v1/execution-jobs/{job_id}")
    assert delete_resp.status_code == 204

    get_resp = client.get(f"/api/v1/execution-jobs/{job_id}")
    assert get_resp.status_code == 404


def test_delete_active_job_cancels_it(client):
    job = ExecutionJob(
        id="job-active-cancel",
        owner_key="testclient",
        collection_name="Active",
        created_at=time.time(),
        expires_at=time.time() + 60,
        state=ExecutionJobState.QUEUED,
    )
    get_job_store().create(job)

    delete_resp = client.delete(f"/api/v1/execution-jobs/{job.id}")
    assert delete_resp.status_code == 204

    get_resp = client.get(f"/api/v1/execution-jobs/{job.id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["state"] == "cancelled"


def test_delete_keeps_a_cancelled_job_whose_worker_is_still_running(client):
    # Removing it would release the owner's concurrency slot while the worker
    # still runs (cancel -> delete -> resubmit bypass of F4(b)).
    job = ExecutionJob(
        id="job-cancelled-live-worker",
        owner_key="testclient",
        collection_name="Live",
        created_at=time.time(),
        expires_at=time.time() + 60,
        state=ExecutionJobState.CANCELLED,
        cancel_requested=True,
        worker_active=True,
    )
    get_job_store().create(job)

    assert client.delete(f"/api/v1/execution-jobs/{job.id}").status_code == 204
    get_resp = client.get(f"/api/v1/execution-jobs/{job.id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["state"] == "cancelled"

    get_job_store().mutate(job.id, lambda j: setattr(j, "worker_active", False))
    assert client.delete(f"/api/v1/execution-jobs/{job.id}").status_code == 204
    assert client.get(f"/api/v1/execution-jobs/{job.id}").status_code == 404


def test_other_owners_job_is_404_for_get_and_delete(client):
    job = ExecutionJob(
        id="job-other-owner",
        owner_key="10.9.9.9",
        collection_name="NotMine",
        created_at=time.time(),
        expires_at=time.time() + 60,
        state=ExecutionJobState.QUEUED,
    )
    get_job_store().create(job)

    get_resp = client.get(f"/api/v1/execution-jobs/{job.id}")
    assert get_resp.status_code == 404

    delete_resp = client.delete(f"/api/v1/execution-jobs/{job.id}")
    assert delete_resp.status_code == 404


def test_unresolved_variables_return_422(client):
    collection = pm_collection("Unresolved", [pm_request("Ping", "GET", "https://{{host}}/ping")])
    resp = client.post(
        "/api/v1/execution-jobs",
        data={"confirm": "true", "supplied_values_json": "{}"},
        files={"collection": ("c.json", json.dumps(collection).encode(), "application/json")},
    )
    assert resp.status_code == 422


def test_blocked_destination_job_ends_failed(client):
    collection = pm_collection("Blocked", [pm_request("Ping", "GET", "https://127.0.0.1/x")])
    resp = client.post(
        "/api/v1/execution-jobs",
        data={"confirm": "true", "supplied_values_json": "{}"},
        files={"collection": ("c.json", json.dumps(collection).encode(), "application/json")},
    )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    status = client.get(f"/api/v1/execution-jobs/{job_id}").json()
    assert status["state"] == "failed"
    assert status["error_code"] == "destination_validation_failed"


def test_idempotency_key_returns_the_same_job(client):
    headers = {"Idempotency-Key": "same-key"}
    first = client.post(
        "/api/v1/execution-jobs", data={"confirm": "true", "supplied_values_json": "{}"},
        files={"collection": ("c.json", json.dumps(_collection()).encode(), "application/json")},
        headers=headers,
    )
    second = client.post(
        "/api/v1/execution-jobs", data={"confirm": "true", "supplied_values_json": "{}"},
        files={"collection": ("c.json", json.dumps(_collection()).encode(), "application/json")},
        headers=headers,
    )
    assert first.json()["job_id"] == second.json()["job_id"]


def test_coerce_supplied_values_canonicalizes_numbers_and_booleans():
    # The HTTP path never echoes a resolved variable value back (per the
    # project-wide no-secrets-in-responses rule), so the canonicalization
    # itself is only observable by calling the conversion helper directly.
    assert _coerce_supplied_values({"flag": True, "count": 1, "ratio": 1.5, "name": "raw"}) == {
        "flag": "true",
        "count": "1",
        "ratio": "1.5",
        "name": "raw",
    }


def test_supplied_boolean_value_is_accepted(client):
    collection = pm_collection("Flagged", [pm_request("Ping", "GET", "https://93.184.216.34/ping?flag={{flag}}")])
    resp = client.post(
        "/api/v1/execution-jobs",
        data={"confirm": "true", "supplied_values_json": json.dumps({"flag": True})},
        files={"collection": ("c.json", json.dumps(collection).encode(), "application/json")},
    )
    assert resp.status_code == 202


def test_supplied_null_value_is_rejected(client):
    collection = pm_collection("Nullish", [pm_request("Ping", "GET", "https://93.184.216.34/ping?x={{x}}")])
    resp = client.post(
        "/api/v1/execution-jobs",
        data={"confirm": "true", "supplied_values_json": json.dumps({"x": None})},
        files={"collection": ("c.json", json.dumps(collection).encode(), "application/json")},
    )
    assert resp.status_code == 422


def test_supplied_array_value_is_rejected(client):
    collection = pm_collection("Arrayish", [pm_request("Ping", "GET", "https://93.184.216.34/ping?x={{x}}")])
    resp = client.post(
        "/api/v1/execution-jobs",
        data={"confirm": "true", "supplied_values_json": json.dumps({"x": [1]})},
        files={"collection": ("c.json", json.dumps(collection).encode(), "application/json")},
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_supplied_nan_or_infinity_is_rejected(client, literal):
    collection = pm_collection("Nanish", [pm_request("Ping", "GET", "https://93.184.216.34/ping?x={{x}}")])
    resp = client.post(
        "/api/v1/execution-jobs",
        data={"confirm": "true", "supplied_values_json": '{"x": ' + literal + "}"},
        files={"collection": ("c.json", json.dumps(collection).encode(), "application/json")},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_error"


def test_existing_inspect_and_analyses_endpoints_still_work(client):
    inspect_resp = client.post(
        "/api/v1/execution-jobs/inspect",
        files={"collection": ("c.json", json.dumps(_collection()).encode(), "application/json")},
    )
    assert inspect_resp.status_code == 200

    from tests.fixtures import builders as b
    scenario = b.report("Smoke", [b.execution("Ping", "GET", "https://api.example.com/ping", position=0)])
    analyses_resp = client.post(
        "/api/v1/analyses",
        files=[("files", ("baseline.json", json.dumps(scenario).encode(), "application/json"))],
    )
    assert analyses_resp.status_code == 201


class _RecordingDispatcher:
    def __init__(self):
        self.submitted = []

    def submit(self, fn, /, *args, **kwargs):
        self.submitted.append((fn, args, kwargs))


def test_create_returns_queued_and_hands_the_run_to_the_dispatcher():
    app = create_app()
    recorder = _RecordingDispatcher()
    app.dependency_overrides[get_newman_runner] = canned_fake_runner
    app.dependency_overrides[get_job_dispatcher] = lambda: recorder
    client = TestClient(app)
    headers = {"Idempotency-Key": "dispatch-once"}
    responses = [
        client.post("/api/v1/execution-jobs", data={"confirm": "true", "supplied_values_json": "{}"},
                    files={"collection": ("c.json", json.dumps(_collection()).encode(), "application/json")},
                    headers=headers)
        for _ in range(2)
    ]
    first, second = responses
    assert first.status_code == second.status_code == 202
    assert first.json()["state"] == "queued"
    assert first.json()["job_id"] == second.json()["job_id"]
    assert len(recorder.submitted) == 1  # the idempotent repeat schedules nothing
    assert recorder.submitted[0][1][0] == first.json()["job_id"]
