"""OIDC mode: every /api/v1 route needs a valid bearer token, and jobs and
analyses belong to the token's subject."""

import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.api.deps import get_job_dispatcher, get_newman_runner, get_verifier
from app.core.auth import OidcVerifier
from app.core.config import Settings, get_settings
from app.main import create_app
from app.services.fake_newman_runner import canned_fake_runner
from app.services.job_dispatcher import InlineJobDispatcher
from tests.fixtures import builders as b
from tests.fixtures.postman_builders import pm_collection, pm_request

ISSUER = "https://idp.example.com/"
AUDIENCE = "baseline11-api"
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _Jwks:
    def get_signing_key_from_jwt(self, token):
        return type("K", (), {"key": _KEY.public_key()})()


def _token(sub: str) -> str:
    now = int(time.time())
    return jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "sub": sub, "iat": now, "exp": now + 300}, _KEY,
                      algorithm="RS256")


def _auth(sub: str) -> dict:
    return {"Authorization": f"Bearer {_token(sub)}"}


@pytest.fixture
def client():
    settings = Settings(auth_mode="oidc", oidc_issuer=ISSUER, oidc_audience=AUDIENCE)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_verifier] = lambda: OidcVerifier(
        issuer=ISSUER, audience=AUDIENCE, jwks_client=_Jwks())
    app.dependency_overrides[get_newman_runner] = canned_fake_runner
    app.dependency_overrides[get_job_dispatcher] = InlineJobDispatcher
    return TestClient(app)


def _reports() -> list:
    report = json.dumps(b.report("Flow", [b.execution("Ping", "GET", "https://api.example.com/ping", position=0)]))
    return [("files", ("a.json", report.encode(), "application/json"))]


def _create_job(client, headers):
    collection = pm_collection("Smoke", [pm_request("Ping", "GET", "https://93.184.216.34/ping")])
    return client.post(
        "/api/v1/execution-jobs", data={"confirm": "true", "supplied_values_json": "{}"},
        files={"collection": ("c.json", json.dumps(collection).encode(), "application/json")}, headers=headers,
    )


@pytest.mark.parametrize("method,path", [
    ("get", "/api/v1/analyses/whatever"),
    ("get", "/api/v1/execution-jobs/whatever"),
    ("post", "/api/v1/execution-jobs/inspect"),
    ("get", "/api/v1/analyses/whatever/download/jmx"),
])
def test_missing_token_is_401_with_bearer_challenge(client, method, path):
    resp = getattr(client, method)(path)
    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"].startswith("Bearer")
    assert resp.json()["code"] == "unauthorized"


def test_invalid_token_is_401(client):
    resp = client.get("/api/v1/analyses/whatever", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401
    assert "not-a-jwt" not in resp.text


def test_health_stays_public(client):
    assert client.get("/health").status_code == 200


def test_uploaded_analysis_is_private_to_its_owner(client):
    created = client.post("/api/v1/analyses", files=_reports(), headers=_auth("alice"))
    assert created.status_code == 201, created.text
    analysis_id = created.json()["analysis_id"]
    assert client.get(f"/api/v1/analyses/{analysis_id}", headers=_auth("alice")).status_code == 200
    assert client.get(f"/api/v1/analyses/{analysis_id}", headers=_auth("bob")).status_code == 404
    assert client.get(f"/api/v1/analyses/{analysis_id}/download/jmx", headers=_auth("bob")).status_code == 404


def test_execution_job_and_its_analysis_belong_to_the_creator(client):
    created = _create_job(client, _auth("alice"))
    assert created.status_code == 202, created.text
    job_id = created.json()["job_id"]
    assert client.get(f"/api/v1/execution-jobs/{job_id}", headers=_auth("bob")).status_code == 404
    assert client.delete(f"/api/v1/execution-jobs/{job_id}", headers=_auth("bob")).status_code == 404
    status = client.get(f"/api/v1/execution-jobs/{job_id}", headers=_auth("alice")).json()
    assert status["state"] == "ready", status
    analysis_id = status["analysis_id"]
    assert client.get(f"/api/v1/analyses/{analysis_id}", headers=_auth("alice")).status_code == 200
    assert client.get(f"/api/v1/analyses/{analysis_id}", headers=_auth("bob")).status_code == 404


def test_concurrency_cap_is_per_user_not_per_ip(client):
    from app.api.deps import get_job_dispatcher

    class _Hold:
        def submit(self, fn, /, *args, **kwargs):
            pass  # never run: jobs stay queued and keep their slot

    client.app.dependency_overrides[get_job_dispatcher] = _Hold
    for _ in range(2):
        assert _create_job(client, _auth("carol")).status_code == 202
    assert _create_job(client, _auth("carol")).status_code == 429
    assert _create_job(client, _auth("dave")).status_code == 202
