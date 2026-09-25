from fastapi.testclient import TestClient

from app.main import create_app


def test_request_id_is_echoed_when_well_formed():
    client = TestClient(create_app())
    resp = client.get("/health", headers={"X-Request-ID": "abc-123-def-456"})
    assert resp.headers["X-Request-ID"] == "abc-123-def-456"


def test_malformed_request_id_is_replaced():
    client = TestClient(create_app())
    resp = client.get("/health", headers={"X-Request-ID": "<script>" + "x" * 200})
    rid = resp.headers["X-Request-ID"]
    assert rid and "<" not in rid and len(rid) <= 64


def test_request_id_is_generated_when_absent():
    client = TestClient(create_app())
    assert len(client.get("/health").headers["X-Request-ID"]) >= 8


def test_readiness_is_ok_for_the_local_backend():
    client = TestClient(create_app())
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


def test_readiness_reports_unreachable_dependencies_without_details(monkeypatch):
    from app import main

    def _failing_checks(settings):
        return {"job_store": False, "queue": True, "bucket": True}

    monkeypatch.setattr(main, "dependency_checks", _failing_checks)
    resp = TestClient(main.create_app()).get("/health/ready")
    assert resp.status_code == 503
    assert resp.json() == {"status": "not_ready", "checks": {"job_store": False, "queue": True, "bucket": True}}
