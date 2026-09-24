# backend/tests/unit/test_postman_inspector.py
import json

from app.core.config import Settings
from app.domain.enums import VariableSource
from app.services.postman_inspector import inspect_collection
from tests.fixtures.postman_builders import pm_collection, pm_environment, pm_folder, pm_request


def _collection_with_variables():
    login = pm_request(
        "Login", "POST", "https://{{host}}/auth/login",
        headers=[{"key": "Content-Type", "value": "application/json"}],
        body={"mode": "raw", "raw": json.dumps({"user": "{{username}}", "pass": "{{password}}"})},
    )
    profile = pm_request("Get Profile", "GET", "https://{{host}}/me",
                          headers=[{"key": "Authorization", "value": "Bearer {{token}}"}])
    return pm_collection("Login Flow", [pm_folder("Auth", [login, profile])])


def test_inspect_without_environment_reports_all_variables_unresolved():
    raw = json.dumps(_collection_with_variables()).encode()
    inspection = inspect_collection(
        collection_raw=raw, collection_filename="c.json",
        environment_raw=None, environment_filename=None, settings=Settings(),
    )
    assert inspection.collection_name == "Login Flow"
    assert [f.name for f in inspection.folders] == ["Auth"]
    assert inspection.request_count_estimate == 2
    assert set(inspection.unresolved_variable_names) == {"host", "username", "password", "token"}
    password = next(v for v in inspection.variables if v.name == "password")
    assert password.sensitive is True
    assert inspection.target_domains == []


def test_inspect_with_environment_resolves_variables_and_domain():
    collection_raw = json.dumps(_collection_with_variables()).encode()
    env = pm_environment("dev", {
        "host": "93.184.216.34", "username": "alice", "password": "hunter2", "token": "abc",
    })
    environment_raw = json.dumps(env).encode()
    inspection = inspect_collection(
        collection_raw=collection_raw, collection_filename="c.json",
        environment_raw=environment_raw, environment_filename="e.json", settings=Settings(),
    )
    assert inspection.unresolved_variable_names == []
    assert inspection.target_domains == ["93.184.216.34"]


def test_inspect_surfaces_unsupported_features():
    request = pm_request("Secure", "GET", "https://api.example.com/x", auth={"type": "ntlm"})
    raw = json.dumps(pm_collection("Demo", [request])).encode()
    inspection = inspect_collection(
        collection_raw=raw, collection_filename="c.json",
        environment_raw=None, environment_filename=None, settings=Settings(),
    )
    assert len(inspection.unsupported_features) == 1


def test_inspect_resolves_collection_level_variable():
    request = pm_request("List", "GET", "https://api.example.com/{{apiVersion}}/items")
    raw = json.dumps(pm_collection(
        "Versioned API", [request], variables=[{"key": "apiVersion", "value": "v2"}],
    )).encode()
    inspection = inspect_collection(
        collection_raw=raw, collection_filename="c.json",
        environment_raw=None, environment_filename=None, settings=Settings(),
    )
    api_version = next(v for v in inspection.variables if v.name == "apiVersion")
    assert api_version.source == VariableSource.COLLECTION
    assert "apiVersion" not in inspection.unresolved_variable_names


def test_inspect_never_includes_a_variable_value():
    collection_raw = json.dumps(_collection_with_variables()).encode()
    env = pm_environment("dev", {"host": "93.184.216.34", "username": "alice", "password": "hunter2", "token": "abc"})
    inspection = inspect_collection(
        collection_raw=collection_raw, collection_filename="c.json",
        environment_raw=json.dumps(env).encode(), environment_filename="e.json", settings=Settings(),
    )
    dumped = inspection.model_dump_json()
    assert "hunter2" not in dumped
    assert "alice" not in dumped
