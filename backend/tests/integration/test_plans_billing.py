"""Plans end to end: the free allowance (3 analyses, 2 MB files), accounts,
demo checkout with a confirmation email, the larger paid file limit, and the
saved history of paid users."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.api.deps import (
    get_account_store,
    get_job_dispatcher,
    get_mailer,
    get_newman_runner,
)
from app.core.config import MIB, Settings, get_settings
from app.main import create_app
from app.repositories.account_store import AccountStore
from app.services.fake_newman_runner import canned_fake_runner
from app.services.job_dispatcher import InlineJobDispatcher
from app.services.mailer import RecordingMailer
from tests.fixtures.postman_builders import pm_collection, pm_request

PASSWORD = "correct horse battery"


@pytest.fixture
def env(tmp_path):
    settings = Settings(newman_runner="fake", rate_limit_enabled=False, trusted_proxy_hops=0)
    store = AccountStore(":memory:", str(tmp_path / "history"))
    mailer = RecordingMailer()
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_account_store] = lambda: store
    app.dependency_overrides[get_mailer] = lambda: mailer
    app.dependency_overrides[get_newman_runner] = canned_fake_runner
    app.dependency_overrides[get_job_dispatcher] = InlineJobDispatcher
    return TestClient(app), store, mailer


def _collection(name="Booking", padding=0) -> bytes:
    collection = pm_collection(name, [pm_request("Ping", "GET", "https://93.184.216.34/ping")])
    if padding:
        # Many ordinary strings: one huge string would trip the separate
        # per-string safety limit instead of the file limit.
        collection["variable"] = [{"key": f"v{i}", "value": "x" * 1000} for i in range(padding // 1000 + 1)]
    return json.dumps(collection).encode()


def _files(raw: bytes) -> dict:
    return {"collection": ("booking.postman_collection.json", raw, "application/json")}


def _start(client, raw: bytes | None = None):
    return client.post(
        "/api/v1/execution-jobs", data={"confirm": "true", "supplied_values_json": "{}"},
        files=_files(raw or _collection()),
    )


def _inspect(client, raw: bytes | None = None):
    return client.post("/api/v1/execution-jobs/inspect", files=_files(raw or _collection()))


def _register(client, email="ada@example.com", name="Ada Lovelace"):
    resp = client.post("/api/v1/auth/register", json={"name": name, "email": email, "password": PASSWORD})
    assert resp.status_code == 201, resp.text
    return resp


def _buy(client, plan="monthly"):
    resp = client.post("/api/v1/billing/checkout", json={"plan": plan})
    assert resp.status_code == 201, resp.text
    return resp.json()["subscription"]


# --- free allowance ---


def test_anonymous_visitor_gets_three_free_analyses_then_must_choose_a_plan(env):
    client, _, _ = env
    me = client.get("/api/v1/account/me").json()
    assert me["user"] is None and me["plan"] is None
    assert me["limits"] == {"max_file_bytes": 2 * MIB, "max_file_mb": 2, "uses_limit": 3, "uses_used": 0,
                            "uses_remaining": 3, "history_retention_days": None}

    for used in (1, 2, 3):
        assert _start(client).status_code == 202
        assert client.get("/api/v1/account/me").json()["limits"]["uses_remaining"] == 3 - used

    blocked = _start(client)
    assert blocked.status_code == 402
    assert blocked.json()["code"] == "usage_limit_reached"
    # Inspection is blocked too, so the plans show on the first step.
    assert _inspect(client).json()["code"] == "usage_limit_reached"


def test_inspecting_does_not_use_up_the_allowance(env):
    client, _, _ = env
    for _ in range(5):
        assert _inspect(client).status_code == 200
    assert client.get("/api/v1/account/me").json()["limits"]["uses_remaining"] == 3


def test_clearing_cookies_does_not_reset_the_allowance_on_the_same_ip(env):
    client, _, _ = env
    client.app.dependency_overrides[get_settings] = lambda: Settings(
        newman_runner="fake", rate_limit_enabled=False, trusted_proxy_hops=0, free_uses=1,
    )
    assert _start(client).status_code == 202
    client.cookies.clear()
    assert _start(client).status_code == 402


def test_registering_a_new_account_does_not_reset_the_allowance(env):
    client, _, _ = env
    for _ in range(3):
        assert _start(client).status_code == 202
    _register(client)
    assert client.get("/api/v1/account/me").json()["limits"]["uses_remaining"] == 0
    assert _start(client).status_code == 402


def test_free_plan_rejects_files_over_2_mb_before_reading_them(env):
    client, _, _ = env
    big = _collection(padding=2 * MIB + 10)
    resp = _inspect(client, big)
    assert resp.status_code == 413
    body = resp.json()
    assert body["code"] == "plan_file_limit"
    assert "free plan accepts files up to 2.0 MB" in body["detail"]
    assert _start(client, big).json()["code"] == "plan_file_limit"
    # A rejected upload is not a use.
    assert client.get("/api/v1/account/me").json()["limits"]["uses_remaining"] == 3


def test_an_idempotent_repeat_is_not_a_second_use(env):
    client, _, _ = env
    headers = {"Idempotency-Key": "attempt-1"}
    data = {"confirm": "true", "supplied_values_json": "{}"}
    first = client.post("/api/v1/execution-jobs", data=data, files=_files(_collection()), headers=headers)
    again = client.post("/api/v1/execution-jobs", data=data, files=_files(_collection()), headers=headers)
    assert first.json()["job_id"] == again.json()["job_id"]
    assert client.get("/api/v1/account/me").json()["limits"]["uses_remaining"] == 2


# --- accounts ---


def test_register_signs_in_and_sends_a_welcome_email(env):
    client, _, mailer = env
    resp = _register(client, email="Ada@Example.com")
    assert resp.json()["user"]["email"] == "ada@example.com"
    cookie = resp.headers["set-cookie"]
    assert "b11_session=" in cookie and "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    assert client.get("/api/v1/account/me").json()["user"]["name"] == "Ada Lovelace"
    [welcome] = _wait_for(mailer, 1)
    assert welcome["To"] == "ada@example.com"
    assert welcome["From"] == "Baseline Auto Correlation <no-reply@localhost>"


def test_duplicate_email_is_rejected(env):
    client, _, _ = env
    _register(client)
    client.cookies.clear()
    resp = client.post("/api/v1/auth/register",
                       json={"name": "Other", "email": "ADA@example.com", "password": PASSWORD})
    assert resp.status_code == 409
    assert resp.json()["code"] == "email_taken"


@pytest.mark.parametrize("body,field", [
    ({"name": "A", "email": "not-an-email", "password": PASSWORD}, "email"),
    ({"name": "A", "email": "a@example.com", "password": "short"}, "password"),
    ({"name": "   ", "email": "a@example.com", "password": PASSWORD}, "name"),
])
def test_register_validates_input(env, body, field):
    client, _, _ = env
    resp = client.post("/api/v1/auth/register", json=body)
    assert resp.status_code == 422
    assert field in resp.text


def test_login_and_logout(env):
    client, _, _ = env
    _register(client)
    client.post("/api/v1/auth/logout")
    assert client.get("/api/v1/account/me").json()["user"] is None

    wrong = client.post("/api/v1/auth/login", json={"email": "ada@example.com", "password": "wrong password"})
    unknown = client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]

    ok = client.post("/api/v1/auth/login", json={"email": "ADA@example.com", "password": PASSWORD})
    assert ok.status_code == 200
    assert client.get("/api/v1/account/me").json()["user"]["email"] == "ada@example.com"


def test_login_attempts_are_throttled(env):
    client, _, _ = env
    from app.api.v1 import accounts

    accounts._login_limiter.cache_clear()
    client.app.dependency_overrides[get_settings] = lambda: Settings(rate_limit_enabled=True, trusted_proxy_hops=0)
    try:
        codes = [client.post("/api/v1/auth/login", json={"email": "x@example.com", "password": "nope-nope"}).status_code
                 for _ in range(12)]
    finally:
        accounts._login_limiter.cache_clear()
    assert codes[:10] == [401] * 10
    assert codes[-1] == 429


def test_password_reset_flow(env):
    client, store, mailer = env
    _register(client)
    client.cookies.clear()
    unknown = client.post("/api/v1/auth/forgot-password", json={"email": "nobody@example.com"})
    known = client.post("/api/v1/auth/forgot-password", json={"email": "ada@example.com"})
    assert unknown.status_code == known.status_code == 202
    assert unknown.json() == known.json()

    reset = _wait_for(mailer, 2)[1]
    assert reset["Subject"] == "Reset your password"
    text = reset.get_body(preferencelist=("plain",)).get_content()
    token = text.split("reset-password?token=")[1].split()[0]

    resp = client.post("/api/v1/auth/reset-password", json={"token": token, "password": "a brand new secret"})
    assert resp.status_code == 200
    assert client.get("/api/v1/account/me").json()["user"]["email"] == "ada@example.com"
    # The link works once.
    again = client.post("/api/v1/auth/reset-password", json={"token": token, "password": "another secret!"})
    assert again.status_code == 422
    client.post("/api/v1/auth/logout")
    assert client.post("/api/v1/auth/login",
                       json={"email": "ada@example.com", "password": "a brand new secret"}).status_code == 200


# --- plans and checkout ---


def test_plans_catalog(env):
    client, _, _ = env
    catalog = client.get("/api/v1/billing/plans").json()
    assert catalog["free"] == {"uses": 3, "max_file_mb": 2}
    assert [(p["id"], p["price_cents"], p["max_file_mb"], p["retention_days"]) for p in catalog["plans"]] == [
        ("monthly", 900, 50, 7), ("semiannual", 4500, 100, 30), ("yearly", 7900, 200, 90),
    ]
    assert catalog["currency"] == "USD"


def test_checkout_requires_an_account(env):
    client, _, _ = env
    resp = client.post("/api/v1/billing/checkout", json={"plan": "monthly"})
    assert resp.status_code == 401


def test_unknown_plan_is_rejected(env):
    client, _, _ = env
    _register(client)
    resp = client.post("/api/v1/billing/checkout", json={"plan": "lifetime"})
    assert resp.status_code == 400
    assert resp.json()["code"] == "purchase_failed"


def test_purchase_activates_the_plan_and_emails_a_confirmation(env):
    client, _, mailer = env
    _register(client)
    sub = _buy(client, "semiannual")
    assert sub["status"] == "active" and sub["plan_name"] == "6 months"
    assert sub["payment_ref"].startswith("demo_")
    assert 180 <= (sub["expires_at"] - sub["starts_at"]) / 86400 <= 185

    me = client.get("/api/v1/account/me").json()
    assert me["plan"]["plan"] == "semiannual"
    assert me["limits"]["max_file_mb"] == 100
    assert me["limits"]["uses_remaining"] is None
    assert me["limits"]["history_retention_days"] == 30

    confirmation = _wait_for(mailer, 2)[1]
    assert confirmation["Subject"] == "Your 6 months plan is confirmed"
    text = confirmation.get_body(preferencelist=("plain",)).get_content()
    assert "$45.00" in text and "100 MB per file" in text and "30 days" in text


def test_a_second_purchase_starts_when_the_current_plan_ends(env):
    client, _, _ = env
    _register(client)
    first = _buy(client, "monthly")
    second = _buy(client, "yearly")
    assert second["status"] == "scheduled"
    assert second["starts_at"] == first["expires_at"]
    me = client.get("/api/v1/account/me").json()
    assert me["plan"]["plan"] == "monthly"
    assert [u["plan"] for u in me["upcoming"]] == ["yearly"]
    items = client.get("/api/v1/billing/subscriptions").json()["items"]
    assert [i["plan"] for i in items] == ["yearly", "monthly"]


def test_paid_plan_lifts_the_use_limit_and_accepts_larger_files(env):
    client, _, _ = env
    _register(client)
    for _ in range(3):
        assert _start(client).status_code == 202
    assert _start(client).status_code == 402
    _buy(client, "monthly")
    assert _start(client, _collection(padding=3 * MIB)).status_code == 202
    over = _inspect(client, _collection(padding=50 * MIB + 10))
    assert over.status_code == 413
    assert "Monthly plan accepts files up to 50 MB" in over.json()["detail"]


# --- history ---


def _run_to_analysis(client, raw: bytes | None = None) -> tuple[str, str]:
    job = _start(client, raw).json()
    status = client.get(f"/api/v1/execution-jobs/{job['job_id']}").json()
    assert status["state"] == "ready", status
    return job["job_id"], status["analysis_id"]


def test_paid_runs_are_saved_to_history_with_downloadable_files(env):
    client, _, _ = env
    _register(client)
    _buy(client, "monthly")
    raw = _collection("Booking API")
    _, analysis_id = _run_to_analysis(client, raw)
    assert client.post(f"/api/v1/analyses/{analysis_id}/generate", json={}).status_code == 200

    [entry] = client.get("/api/v1/history").json()["items"]
    assert entry["collection_name"] == "Booking API"
    assert entry["collection_bytes"] == len(raw)
    assert entry["jmx_status"] == "generated"
    assert entry["request_count"] >= 1
    assert 6 <= entry["days_left"] <= 7
    assert set(entry["downloads"]) == {"collection", "jmx", "manifest"}

    collection = client.get(entry["downloads"]["collection"])
    assert collection.content == raw
    assert 'filename="booking-api.postman_collection.json"' in collection.headers["content-disposition"]
    jmx = client.get(entry["downloads"]["jmx"])
    assert jmx.status_code == 200 and jmx.content.startswith(b"<?xml")
    assert json.loads(client.get(entry["downloads"]["manifest"]).content)


def test_free_runs_are_not_saved(env):
    client, _, _ = env
    _register(client)
    _run_to_analysis(client)
    assert client.get("/api/v1/history").json()["items"] == []


def test_history_is_private_to_its_owner_and_deletable(env):
    client, _, _ = env
    _register(client)
    _buy(client)
    _run_to_analysis(client)
    [entry] = client.get("/api/v1/history").json()["items"]

    client.post("/api/v1/auth/logout")
    assert client.get("/api/v1/history").status_code == 401
    _register(client, email="eve@example.com", name="Eve")
    assert client.get(entry["downloads"]["collection"]).status_code == 404
    assert client.delete(f"/api/v1/history/{entry['id']}").status_code == 404

    client.post("/api/v1/auth/logout")
    client.post("/api/v1/auth/login", json={"email": "ada@example.com", "password": PASSWORD})
    assert client.delete(f"/api/v1/history/{entry['id']}").status_code == 204
    assert client.get("/api/v1/history").json()["items"] == []


def test_history_retention_follows_the_plan(env):
    client, store, _ = env
    _register(client)
    _buy(client, "yearly")
    _run_to_analysis(client)
    [entry] = client.get("/api/v1/history").json()["items"]
    assert 89 <= entry["days_left"] <= 90
    # Past its retention the entry is gone from the list and its files are removed.
    assert store.purge_expired_history(now=time.time() + 91 * 86400) == 1
    assert not store.entry_dir(entry["id"]).exists()


def _wait_for(mailer: RecordingMailer, count: int, timeout: float = 5.0) -> list:
    deadline = time.time() + timeout
    while len(mailer.sent) < count and time.time() < deadline:
        time.sleep(0.01)
    assert len(mailer.sent) >= count, [m["Subject"] for m in mailer.sent]
    return mailer.sent
