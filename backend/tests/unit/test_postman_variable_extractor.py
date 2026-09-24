from app.services.postman_variable_extractor import extract_variable_references
from tests.fixtures.postman_builders import pm_collection, pm_folder, pm_request


def test_extracts_variables_from_url_headers_body_and_auth():
    login = pm_request(
        "Login", "POST", "https://{{host}}/auth/login",
        headers=[{"key": "Content-Type", "value": "application/json"}],
        body={"mode": "raw", "raw": '{"user": "{{username}}", "pass": "{{password}}"}'},
    )
    profile = pm_request(
        "Get Profile", "GET", "https://{{host}}/me",
        headers=[{"key": "Authorization", "value": "Bearer {{token}}"}],
        auth={"type": "bearer", "bearer": [{"key": "token", "value": "{{token}}", "type": "string"}]},
    )
    collection = pm_collection("Login Flow", [pm_folder("Auth", [login, profile])])

    refs = extract_variable_references(collection)
    names = {r.name for r in refs}
    assert names == {"host", "username", "password", "token"}
    assert any(r.name == "host" and "Login" in r.location for r in refs)
    assert any(r.name == "token" and "auth" in r.location for r in refs)


def test_extracts_variables_from_query_and_path_object_url():
    request = pm_request("Get", "GET", {
        "raw": "https://api.example.com/orders/:id?limit={{pageSize}}",
        "host": ["api", "example", "com"],
        "path": ["orders", ":id"],
        "variable": [{"key": "id", "value": "{{orderId}}"}],
        "query": [{"key": "limit", "value": "{{pageSize}}"}],
    })
    collection = pm_collection("Demo", [request])
    names = {r.name for r in extract_variable_references(collection)}
    assert names == {"pageSize", "orderId"}


def test_ignores_file_form_fields_and_returns_urlencoded_values():
    request = pm_request("Upload", "POST", "https://api.example.com/upload", body={
        "mode": "formdata",
        "formdata": [
            {"key": "file", "type": "file", "src": "/tmp/x.png"},
            {"key": "note", "type": "text", "value": "{{noteText}}"},
        ],
    })
    collection = pm_collection("Demo", [request])
    names = {r.name for r in extract_variable_references(collection)}
    assert names == {"noteText"}


def test_no_variables_returns_empty_list():
    request = pm_request("Ping", "GET", "https://api.example.com/ping")
    collection = pm_collection("Demo", [request])
    assert extract_variable_references(collection) == []
