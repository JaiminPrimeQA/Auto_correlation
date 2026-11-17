"""HAR upload to correlated JMX through the public API."""

import json

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_account_store
from app.core.config import MIB, Settings, get_settings
from app.main import create_app
from app.repositories.account_store import AccountStore
from tests.fixtures.har_builders import API, APP, entry, har, login_flow

PASSWORD = "s3cret-pass-value"


@pytest.fixture
def client() -> TestClient:
    settings = Settings(rate_limit_enabled=False, trusted_proxy_hops=0)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


@pytest.fixture
def account_client(tmp_path) -> TestClient:
    settings = Settings(rate_limit_enabled=False, trusted_proxy_hops=0, require_account=True)
    store = AccountStore(":memory:", str(tmp_path / "history"))
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_account_store] = lambda: store
    client = TestClient(app)
    client.post("/api/v1/auth/register", json={"name": "Ada", "email": "ada@example.com", "password": "correct horse battery"})
    return client


def upload(raw: bytes, name="flow.har"):
    return {"files": (name, raw, "application/json")}


def inspect(client, raw):
    response = client.post("/api/v1/analyses/har/inspect", files=upload(raw))
    assert response.status_code == 200, response.text
    return response.json()


def create(client, raw, selected):
    return client.post("/api/v1/analyses/har", files=upload(raw), data={"selected": json.dumps(selected)})


def ticked(inspection):
    return [e["index"] for e in inspection["files"][0]["entries"] if e["decision"] == "keep"]


def test_inspect_summarises_and_lists_only_what_can_be_chosen(client):
    result = inspect(client, har(login_flow()))["files"][0]
    assert result["total"] == 13
    assert (result["kept"], result["optional"], result["removed"]) == (3, 2, 8)
    assert result["removed_by_reason"]["Image"] == 2
    assert all(e["decision"] != "removed" for e in result["entries"])


def test_recording_becomes_a_correlated_plan_with_no_password_in_it(client):
    raw = har(login_flow(password=PASSWORD))
    summary = create(client, raw, ticked(inspect(client, raw)))
    assert summary.status_code == 201, summary.text
    analysis_id = summary.json()["analysis_id"]

    correlated = client.post(f"/api/v1/analyses/{analysis_id}/auto-correlate")
    assert correlated.status_code == 200, correlated.text
    assert correlated.json()["total"] >= 1

    generated = client.post(f"/api/v1/analyses/{analysis_id}/generate", json={})
    assert generated.status_code == 200, generated.text
    jmx = client.get(f"/api/v1/analyses/{analysis_id}/download/jmx").text
    assert "JSONPostProcessor" in jmx
    assert "${__P(Code,)}" in jmx
    assert PASSWORD not in jmx
    assert "wrong-pass" not in jmx
    assert "main.js" not in jmx


def correlate(client, entries):
    raw = har(entries)
    analysis_id = create(client, raw, ticked(inspect(client, raw))).json()["analysis_id"]
    return client.post(f"/api/v1/analyses/{analysis_id}/auto-correlate").json(), analysis_id


def test_value_repeated_at_two_depths_is_correlated(client):
    guid = "6d099995-e2f9-4a01-bef1-258eb99c1b77"
    body = json.dumps({"data": {"merchantGuid": guid}, "merchantGuid": guid})
    result, _ = correlate(client, [
        entry("GET", f"{API}/api/merchant", response=body),
        entry("GET", f"{API}/api/next", response="{}", headers=[{"name": "x-merchantid", "value": guid}]),
    ])
    assert result["variables"] == ["merchantGuid"]


def test_value_in_sibling_objects_stays_ambiguous(client):
    guid = "6d099995-e2f9-4a01-bef1-258eb99c1b77"
    result, _ = correlate(client, [
        entry("GET", f"{API}/api/merchant", response=json.dumps({"a": {"id": guid}, "b": {"id": guid}})),
        entry("GET", f"{API}/api/next", response="{}", headers=[{"name": "x-thing", "value": guid}]),
    ])
    assert result["total"] == 0


def test_value_shared_by_different_fields_stays_ambiguous(client):
    guid = "6d099995-e2f9-4a01-bef1-258eb99c1b77"
    result, _ = correlate(client, [
        entry("GET", f"{API}/api/merchant", response=json.dumps({"a": guid, "b": guid})),
        entry("GET", f"{API}/api/next", response="{}", headers=[{"name": "x-thing", "value": guid}]),
    ])
    assert result["total"] == 0


def test_route_word_matching_a_menu_label_is_not_correlated(client):
    result, _ = correlate(client, [
        entry("GET", f"{API}/api/menus", response=json.dumps({"data": [{"icon": "dashboard"}]})),
        entry("GET", f"{API}/api/dashboard/history", response="{}"),
    ])
    assert result["total"] == 0


def test_identifier_in_a_path_is_still_correlated(client):
    result, _ = correlate(client, [
        entry("GET", f"{API}/api/orders", response=json.dumps({"orderRef": "ord-8f3k29x7"})),
        entry("GET", f"{API}/api/orders/ord-8f3k29x7", response="{}"),
    ])
    assert result["variables"] == ["orderRef"]


def test_empty_sensitive_header_is_not_asked_for_at_run_time(client):
    _, analysis_id = correlate(client, [
        entry("GET", f"{API}/api/public", response="{}", headers=[{"name": "x-tokenguid", "value": ""}]),
    ])
    client.post(f"/api/v1/analyses/{analysis_id}/generate", json={})
    assert "__P(x_tokenguid" not in client.get(f"/api/v1/analyses/{analysis_id}/download/jmx").text


def test_uppercase_code_in_a_path_is_still_correlated(client):
    result, _ = correlate(client, [
        entry("GET", f"{API}/api/rooms", response=json.dumps({"roomCode": "XQZPLMKT"})),
        entry("GET", f"{API}/api/rooms/XQZPLMKT", response="{}"),
    ])
    assert result["variables"] == ["roomCode"]


def test_password_is_masked_when_browsing_the_analysis(client):
    raw = har(login_flow(password=PASSWORD))
    analysis_id = create(client, raw, ticked(inspect(client, raw))).json()["analysis_id"]
    executions = client.get(f"/api/v1/analyses/{analysis_id}/executions").json()["items"]
    login = next(e for e in executions if e["method"] == "POST")
    detail = client.get(f"/api/v1/analyses/{analysis_id}/executions/{login['id']}")
    occurrences = client.get(f"/api/v1/analyses/{analysis_id}/executions/{login['id']}/occurrences?side=request")
    assert PASSWORD not in detail.text
    assert PASSWORD not in occurrences.text
    assert "tester" in detail.text  # only the credential is hidden


def test_warnings_are_listed_once(client):
    raw = har(login_flow())
    warnings = create(client, raw, ticked(inspect(client, raw))).json()["warnings"]
    assert len(warnings) == len(set(warnings))


def test_recorded_session_cookie_never_reaches_the_plan(client):
    entries = [entry("GET", f"{API}/api/x", response="{}",
                     headers=[{"name": "Cookie", "value": "ASP.NET_SessionId=live-secret-value"}])]
    _, analysis_id = correlate(client, entries)
    client.post(f"/api/v1/analyses/{analysis_id}/generate", json={})
    assert "live-secret-value" not in client.get(f"/api/v1/analyses/{analysis_id}/download/jmx").text


def test_second_recording_with_none_of_the_selected_requests_is_refused(client):
    first = har(login_flow())
    other = har([entry("GET", f"{API}/api/unrelated", response="{}")])
    response = client.post(
        "/api/v1/analyses/har", data={"selected": json.dumps(ticked(inspect(client, first)))},
        files=[("files", ("a.har", first, "application/json")), ("files", ("b.har", other, "application/json"))],
    )
    assert response.status_code == 422
    assert "second recording" in response.json()["detail"]


def test_oversize_recording_is_refused(tmp_path):
    settings = Settings(rate_limit_enabled=False, trusted_proxy_hops=0, max_har_bytes=10)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    response = TestClient(app).post("/api/v1/analyses/har/inspect", files=upload(har(login_flow())))
    assert response.status_code == 413


def plan_for(client, entries, **generate):
    _, analysis_id = correlate(client, entries)
    assert client.post(f"/api/v1/analyses/{analysis_id}/generate", json=generate).status_code == 200
    return client.get(f"/api/v1/analyses/{analysis_id}/download/jmx").text


def test_plan_checks_business_success_and_stops_a_failed_login(client):
    jmx = plan_for(client, login_flow())
    assert '<stringProp name="JSON_PATH">$.data.isLoginSuccessful</stringProp>' in jmx
    assert '<stringProp name="JSON_PATH">$.isSuccess</stringProp>' in jmx
    assert jmx.count('testname="Stop this user if login failed"') == 1
    assert 'testname="Require Code"' in jmx


def test_plan_is_ready_for_a_browser_visitor(client):
    agent = [{"name": "user-agent", "value": "Mozilla/5.0 Test"}]
    jmx = plan_for(client, [entry("GET", f"{API}/api/x", response="{}", headers=agent)])
    assert "Mozilla/5.0 Test" in jmx
    assert '<boolProp name="CookieManager.clearEachIteration">true</boolProp>' in jmx
    assert 'testname="View Results Tree"' in jmx


def test_user_agent_can_still_be_turned_off(client):
    agent = [{"name": "user-agent", "value": "Mozilla/5.0 Test"}]
    jmx = plan_for(client, [entry("GET", f"{API}/api/x", response="{}", headers=agent)], keep_user_agent=False)
    assert "Mozilla/5.0 Test" not in jmx


TOKEN = "abcdEFGH1234567890tokenvalue"


@pytest.mark.parametrize("producer,consumer,variable", [
    (entry("GET", f"{APP}/page", mime="text/html", response=f'<meta name="csrf-token" content="{TOKEN}">'),
     entry("POST", f"{API}/api/save", body="{}", response="{}", headers=[{"name": "X-CSRF-Token", "value": TOKEN}]),
     "csrf_token"),
    (entry("GET", f"{APP}/page", mime="text/html", response=f'<meta content="{TOKEN}" name="csrf-token">'),
     entry("POST", f"{API}/api/save", body="{}", response="{}", headers=[{"name": "X-CSRF-Token", "value": TOKEN}]),
     "csrf_token"),
    (entry("GET", f"{APP}/page", mime="text/html", response=f'<script>window.cfg = {{"sessionKey":"{TOKEN}"}};</script>'),
     entry("GET", f"{API}/api/data?key={TOKEN}", response="{}"), "sessionKey"),
    (entry("GET", f"{APP}/page", mime="text/html", response=f"<script>var apiKey = '{TOKEN}';</script>"),
     entry("GET", f"{API}/api/data?key={TOKEN}", response="{}"), "apiKey"),
    (entry("POST", f"{API}/api/start", body="{}", response=json.dumps({"ticket": "ab/cd+ef==1234567890"})),
     entry("GET", f"{API}/api/next?ticket=ab%2Fcd%2Bef%3D%3D1234567890", response="{}"), "ticket"),
])
def test_values_outside_plain_json_are_correlated(client, producer, consumer, variable):
    result, _ = correlate(client, [producer, consumer])
    assert result["variables"] == [variable]


def test_form_field_keeps_its_own_source_alongside_script_values(client):
    html = f'<input name="__VIEWSTATE" value="{TOKEN}" /><script>var v = "{TOKEN}";</script>'
    result, _ = correlate(client, [
        entry("GET", f"{APP}/form", mime="text/html", response=html),
        entry("POST", f"{APP}/form", mime="text/html", response="<html></html>", body=f"__VIEWSTATE={TOKEN}",
              post_mime="application/x-www-form-urlencoded"),
    ])
    assert result["variables"] == ["VIEWSTATE"]


def test_code_in_a_redirect_url_is_correlated(client):
    jmx_entries = [
        entry("GET", f"{APP}/start", status=302, mime="text/html", redirect=f"{APP}/cb?code={TOKEN}", resource="document"),
        entry("GET", f"{APP}/cb?code={TOKEN}", mime="text/html", response="<html>ok</html>", resource="document"),
        entry("POST", f"{API}/api/exchange", body=json.dumps({"code": TOKEN}), response="{}"),
    ]
    result, _ = correlate(client, jmx_entries)
    assert result["variables"] == ["code"]
    assert '<stringProp name="Sample.scope">all</stringProp>' in plan_for(client, jmx_entries)


def test_browser_headers_are_never_correlated(client):
    agent = "Mozilla/5.0 (Windows NT 10.0) Chrome"
    echo = {"origin": "https://app.example.com", "language": "en-US,en;q=0.9,fr;q=0.8", "client": agent}
    result, _ = correlate(client, [
        entry("GET", f"{API}/api/settings", response=json.dumps(echo)),
        entry("GET", f"{API}/api/next", response="{}", headers=[
            {"name": "origin", "value": echo["origin"]}, {"name": "accept-language", "value": echo["language"]},
            {"name": "x-user-agent", "value": agent},
        ]),
    ])
    assert result["total"] == 0


def test_password_has_a_visible_row_in_the_plan(client):
    jmx = plan_for(client, login_flow())
    assert '<stringProp name="Argument.name">Code</stringProp>' in jmx
    assert '<stringProp name="Argument.value">${__P(Code,)}</stringProp>' in jmx
    assert '"Code": "${Code}"' in jmx


def test_two_recordings_use_the_two_run_engine(client):
    first = har(login_flow(token="tok-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"))
    second = har(login_flow(token="tok-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"))
    selected = ticked(inspect(client, first))
    response = client.post(
        "/api/v1/analyses/har", data={"selected": json.dumps(selected)},
        files=[("files", ("a.har", first, "application/json")), ("files", ("b.har", second, "application/json"))],
    )
    assert response.status_code == 201, response.text
    assert response.json()["mode"] == "two_run"


def test_selected_must_be_a_list_of_requests(client):
    raw = har(login_flow())
    assert create(client, raw, []).status_code == 422
    bad = client.post("/api/v1/analyses/har", files=upload(raw), data={"selected": "{}"})
    assert bad.status_code == 422


def test_a_newman_report_is_not_accepted_as_a_recording(client):
    response = client.post("/api/v1/analyses/har/inspect", files=upload(b'{"run": {"executions": []}}'))
    assert response.status_code == 422


class TestPlanLimits:
    def big_recording(self) -> bytes:
        padding = "x" * (3 * MIB)
        return har([*login_flow(), entry("GET", f"{APP}/huge.js", mime="application/javascript", resource="script",
                                         response=padding)])

    def test_free_user_can_convert_a_large_recording_that_cleans_up_small(self, account_client):
        raw = self.big_recording()
        assert len(raw) > 2 * MIB
        selected = ticked(inspect(account_client, raw))
        assert create(account_client, raw, selected).status_code == 201

    def test_cleaned_size_over_the_plan_limit_is_refused(self, account_client):
        raw = har([entry("POST", f"{APP}/api/big", body="{}", response="x" * (3 * MIB))])
        response = create(account_client, raw, [0])
        assert response.status_code == 413
        assert response.json()["code"] == "plan_file_limit"

    def test_inspecting_does_not_use_a_free_analysis(self, account_client):
        before = account_client.get("/api/v1/account/me").json()["limits"]["uses_remaining"]
        inspect(account_client, har(login_flow()))
        assert account_client.get("/api/v1/account/me").json()["limits"]["uses_remaining"] == before

    def test_signed_out_user_is_asked_to_sign_in(self, tmp_path):
        settings = Settings(rate_limit_enabled=False, trusted_proxy_hops=0, require_account=True)
        app = create_app()
        app.dependency_overrides[get_settings] = lambda: settings
        response = TestClient(app).post("/api/v1/analyses/har/inspect", files=upload(har(login_flow())))
        assert response.status_code == 401
