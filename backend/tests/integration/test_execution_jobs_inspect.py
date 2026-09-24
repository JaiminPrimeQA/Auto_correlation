import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.fixtures.postman_builders import pm_collection, pm_environment, pm_folder, pm_request


@pytest.fixture
def client():
    return TestClient(create_app())


def _collection_with_variables():
    login = pm_request(
        "Login", "POST", "https://{{host}}/auth/login",
        headers=[{"key": "Content-Type", "value": "application/json"}],
        body={"mode": "raw", "raw": json.dumps({"user": "{{username}}", "pass": "{{password}}"})},
    )
    profile = pm_request("Get Profile", "GET", "https://{{host}}/me",
                          headers=[{"key": "Authorization", "value": "Bearer {{token}}"}])
    return pm_collection("Login Flow", [pm_folder("Auth", [login, profile])])


def _files(collection, environment=None):
    files = [("collection", ("collection.json", json.dumps(collection).encode(), "application/json"))]
    if environment is not None:
        files.append(("environment", ("environment.json", json.dumps(environment).encode(), "application/json")))
    return files


def test_inspect_reports_folders_and_unresolved_variables(client):
    resp = client.post("/api/v1/execution-jobs/inspect", files=_files(_collection_with_variables()))
    assert resp.status_code == 200
    body = resp.json()
    assert body["collection_name"] == "Login Flow"
    assert body["request_count_estimate"] == 2
    assert [f["name"] for f in body["folders"]] == ["Auth"]
    assert {v["name"] for v in body["variables"]} == {"host", "username", "password", "token"}
    assert set(body["unresolved_variable_names"]) == {"host", "username", "password", "token"}
    password_var = next(v for v in body["variables"] if v["name"] == "password")
    assert password_var["sensitive"] is True


def test_inspect_resolves_variables_from_environment_and_never_echoes_values(client):
    env = pm_environment("dev", {"host": "93.184.216.34", "username": "alice", "password": "hunter2", "token": "abc"})
    resp = client.post("/api/v1/execution-jobs/inspect", files=_files(_collection_with_variables(), env))
    assert resp.status_code == 200
    body = resp.json()
    assert body["unresolved_variable_names"] == []
    assert body["target_domains"] == ["93.184.216.34"]
    assert "hunter2" not in resp.text
    assert "alice" not in resp.text


def test_inspect_reports_blocked_localhost_target_as_a_warning_not_a_failure(client):
    collection = pm_collection("Local", [pm_request("Ping", "GET", "https://localhost/health")])
    resp = client.post("/api/v1/execution-jobs/inspect", files=_files(collection))
    assert resp.status_code == 200
    body = resp.json()
    assert body["target_domains"] == []
    assert any("blocked" in w.lower() for w in body["domain_warnings"])


def test_inspect_rejects_malformed_collection_with_actionable_error(client):
    resp = client.post(
        "/api/v1/execution-jobs/inspect",
        files=[("collection", ("c.json", b'{"not": "a collection"}', "application/json"))],
    )
    assert resp.status_code == 422
    assert resp.json()["errors"][0]["path"] == "$.info.name"


def test_existing_direct_newman_report_upload_still_works(client):
    from tests.fixtures import builders as b

    scenario = b.report("Smoke", [b.execution("Ping", "GET", "https://api.example.com/ping", position=0)])
    raw = json.dumps(scenario).encode()
    resp = client.post("/api/v1/analyses", files=[("files", ("baseline.json", raw, "application/json"))])
    assert resp.status_code == 201
