# backend/tests/unit/test_postman_unsupported.py
from app.domain.enums import UnsupportedFeatureKind
from app.services.postman_unsupported import detect_unsupported_features
from tests.fixtures.postman_builders import pm_collection, pm_folder, pm_request


def test_flags_file_form_field():
    request = pm_request("Upload", "POST", "https://api.example.com/upload", body={
        "mode": "formdata",
        "formdata": [{"key": "file", "type": "file", "src": "/tmp/x.png"}],
    })
    findings = detect_unsupported_features(pm_collection("Demo", [request]))
    assert findings[0].kind == UnsupportedFeatureKind.LOCAL_DATA_FILE


def test_flags_ntlm_auth_as_interactive():
    request = pm_request("Secure", "GET", "https://api.example.com/x", auth={"type": "ntlm"})
    findings = detect_unsupported_features(pm_collection("Demo", [request]))
    assert findings[0].kind == UnsupportedFeatureKind.INTERACTIVE_AUTH


def test_flags_oauth2_implicit_grant_as_interactive():
    request = pm_request("Secure", "GET", "https://api.example.com/x", auth={
        "type": "oauth2",
        "oauth2": [{"key": "grantType", "value": "implicit"}],
    })
    findings = detect_unsupported_features(pm_collection("Demo", [request]))
    assert findings[0].kind == UnsupportedFeatureKind.INTERACTIVE_AUTH


def test_does_not_flag_oauth2_client_credentials():
    request = pm_request("Secure", "GET", "https://api.example.com/x", auth={
        "type": "oauth2",
        "oauth2": [{"key": "grantType", "value": "client_credentials"}],
    })
    assert detect_unsupported_features(pm_collection("Demo", [request])) == []


def test_flags_non_http_protocol():
    request = pm_request("Socket", "GET", "wss://api.example.com/socket")
    findings = detect_unsupported_features(pm_collection("Demo", [request]))
    assert findings[0].kind == UnsupportedFeatureKind.NON_HTTP_PROTOCOL


def test_flags_pm_send_request_in_scripts():
    request = pm_request("Chained", "GET", "https://api.example.com/x", events=[
        {"listen": "prerequest", "script": {"exec": ["pm.sendRequest('https://x', function(e,r){});"]}},
    ])
    findings = detect_unsupported_features(pm_collection("Demo", [request]))
    assert findings[0].kind == UnsupportedFeatureKind.DYNAMIC_REQUEST_CONSTRUCTION


def test_flags_client_certificates_on_collection_or_environment():
    collection = pm_collection("Demo", [pm_request("Ping", "GET", "https://api.example.com/ping")])
    collection["clientCertificates"] = [{"name": "cert"}]
    findings = detect_unsupported_features(collection)
    assert findings[0].kind == UnsupportedFeatureKind.CLIENT_CERTIFICATE


def test_supported_collection_has_no_findings():
    request = pm_request("Ping", "GET", "https://api.example.com/ping")
    assert detect_unsupported_features(pm_collection("Demo", [request])) == []


def test_flags_folder_level_auth_as_interactive():
    request = pm_request("Ping", "GET", "https://api.example.com/ping")
    folder = pm_folder("Secure Folder", [request])
    folder["auth"] = {"type": "ntlm"}
    findings = detect_unsupported_features(pm_collection("Demo", [folder]))
    assert findings[0].kind == UnsupportedFeatureKind.INTERACTIVE_AUTH
    assert findings[0].location == "Secure Folder"


def test_oauth2_with_null_params_does_not_crash():
    # "oauth2": null is a valid-but-empty shape a hand-edited collection could
    # have; auth.get("oauth2", []) only defaults for a MISSING key, not one
    # explicitly set to None, so this used to raise TypeError: NoneType not iterable.
    request = pm_request("Secure", "GET", "https://api.example.com/x", auth={"type": "oauth2", "oauth2": None})
    findings = detect_unsupported_features(pm_collection("Demo", [request]))
    assert findings == []


def test_script_exec_with_null_entry_does_not_crash_and_still_detects_send_request():
    request = pm_request("Chained", "GET", "https://api.example.com/x", events=[
        {"listen": "prerequest", "script": {"exec": [None, "pm.sendRequest('https://x', function(e,r){});"]}},
    ])
    findings = detect_unsupported_features(pm_collection("Demo", [request]))
    assert findings[0].kind == UnsupportedFeatureKind.DYNAMIC_REQUEST_CONSTRUCTION


def test_flags_collection_root_pm_send_request():
    request = pm_request("Ping", "GET", "https://api.example.com/ping")
    collection = pm_collection("Demo", [request])
    collection["event"] = [
        {"listen": "prerequest", "script": {"exec": ["pm.sendRequest('https://x', function(e,r){});"]}},
    ]
    findings = detect_unsupported_features(collection)
    assert findings[0].kind == UnsupportedFeatureKind.DYNAMIC_REQUEST_CONSTRUCTION
    assert findings[0].location == "collection"
