import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.fixtures import builders as b
from tests.fixtures import scenarios

_GUID = "69f873ce-2af2-4780-88c5-74f373e7c8b2"


def _webhook_scenario(_role):
    return b.report("Webhooks", [
        b.execution("GetAllMerchants", "GET", "https://mcapi.test/merchants",
                    resp_body=[{"merchantID": 6, "merchantGUID": _GUID},
                               {"merchantID": 30784, "merchantGUID": "7966c8b8-3844-4e3e-9862-b4b8ec14fded"}],
                    position=0),
        b.execution("GetWebhookReport", "POST", "https://mcapi.test/report",
                    req_body=b.raw_json_body({"MerchantID": "6", "StatusID": -100}), position=1),
        b.execution("GetById", "GET", f"https://mcapi.test/byid?merchantsGuid={_GUID}&webhookID=4585463", position=2),
        b.execution("GetHistory", "GET", "https://mcapi.test/history?merchantsGuid=string&webhookID=6232", position=3),
        b.execution("UpdateStatus", "POST", "https://mcapi.test/update",
                    req_body=b.raw_json_body({"webhookId": 5353, "merchantsGuid": _GUID}), position=4),
    ])


@pytest.fixture
def client():
    return TestClient(create_app())


def _files(scenario):
    b = json.dumps(scenario("baseline")).encode()
    c = json.dumps(scenario("comparison")).encode()
    return [
        ("files", ("baseline.json", b, "application/json")),
        ("files", ("comparison.json", c, "application/json")),
    ]


def test_auto_correlate_endpoint(client):
    # A value that is the SAME across both runs but reused later: the strict
    # engine yields 0 candidates, but one-click auto-correlate threads it.
    def scen(_role):
        return {
            "collection": {"info": {"name": "Thread"}},
            "run": {"id": "r", "executions": [
                {"cursor": {"position": 0}, "item": {"name": "Login"},
                 "request": {"method": "POST", "header": [{"key": "Content-Type", "value": "application/json"}],
                             "url": {"raw": "https://api.test/login", "protocol": "https", "host": ["api", "test"], "path": ["login"]},
                             "body": {"mode": "raw", "raw": "{\"u\":\"a\"}", "options": {"raw": {"language": "json"}}}},
                 "response": {"code": 200, "status": "OK", "header": [{"key": "Content-Type", "value": "application/json"}],
                              "body": "{\"sessionRef\":\"SESS-SAME-9931\"}"}},
                {"cursor": {"position": 1}, "item": {"name": "GetData"},
                 "request": {"method": "GET", "header": [{"key": "X-Session", "value": "SESS-SAME-9931"}],
                             "url": {"raw": "https://api.test/data", "protocol": "https", "host": ["api", "test"], "path": ["data"]}},
                 "response": {"code": 200, "status": "OK", "header": [{"key": "Content-Type", "value": "application/json"}], "body": "{\"ok\":true}"}},
            ]},
        }

    r = client.post("/api/v1/analyses", files=_files(scen))
    aid = r.json()["analysis_id"]
    assert r.json()["candidate_count"] == 0  # strict engine finds nothing
    auto = client.post(f"/api/v1/analyses/{aid}/auto-correlate")
    assert auto.status_code == 200, auto.text
    body = auto.json()
    assert body["created"] == 1
    assert body["variables"] == ["sessionRef"]

    # The graph must reflect the auto-correlate rule as an accepted edge even
    # though it has no source candidate (origin AUTOMATIC).
    graph = client.get(f"/api/v1/analyses/{aid}/graph").json()
    assert graph["stats"]["edges"] >= 1
    assert any(e["variable"] == "sessionRef" and e["state"] == "accepted" for e in graph["edges"])


def test_full_two_run_flow(client):
    r = client.post("/api/v1/analyses", files=_files(scenarios.scenario_login_token))
    assert r.status_code == 201, r.text
    summary = r.json()
    aid = summary["analysis_id"]
    assert summary["mode"] == "two_run"
    assert summary["blocked"] is False

    # executions list + detail
    ex = client.get(f"/api/v1/analyses/{aid}/executions").json()
    assert ex["total"] == 2
    detail = client.get(f"/api/v1/analyses/{aid}/executions/{ex['items'][0]['id']}").json()
    assert detail["response"]["code"] == 200

    # candidates
    cands = client.get(f"/api/v1/analyses/{aid}/candidates").json()
    assert cands["total"] >= 1

    # accept high-confidence
    acc = client.post(f"/api/v1/analyses/{aid}/candidates/accept-high").json()
    assert acc["accepted"] >= 1

    # generate -> "Generated JMX" status (never "validated" without a JMeter run)
    gen = client.post(f"/api/v1/analyses/{aid}/generate", json={}).json()
    assert gen["validation"]["ok"] is True
    assert gen["status"] == "generated"
    assert gen["manifest"]["summary"]["downstream_values_replaced"] >= 1
    # preview is a Draft
    prev = client.post(f"/api/v1/analyses/{aid}/preview", json={}).json()
    assert prev["status"] == "draft"

    # download jmx + manifest
    jmx = client.get(f"/api/v1/analyses/{aid}/download/jmx")
    assert jmx.status_code == 200
    assert "HTTPSamplerProxy" in jmx.text
    man = client.get(f"/api/v1/analyses/{aid}/download/manifest")
    assert man.status_code == 200


def test_all_401_is_blocked(client):
    r = client.post("/api/v1/analyses", files=_files(scenarios.scenario_all_401))
    assert r.status_code == 201
    summary = r.json()
    assert summary["blocked"] is True
    assert any("401" in w for w in summary["comparison_health"]["blockers"])
    # no high-confidence auto candidates from error bodies
    cands = client.get(f"/api/v1/analyses/{summary['analysis_id']}/candidates").json()
    assert all(c["confidence"] != "high" for c in cands["items"])


@pytest.mark.parametrize("action", ["auto-correlate", "candidates/accept-high"])
def test_unhealthy_runs_cannot_claim_automatic_correlation(client, action):
    created = client.post("/api/v1/analyses", files=_files(scenarios.scenario_all_401))
    aid = created.json()["analysis_id"]
    response = client.post(f"/api/v1/analyses/{aid}/{action}")
    assert response.status_code == 422, response.text
    assert "health" in response.json()["detail"].lower()
    summary = client.get(f"/api/v1/analyses/{aid}").json()
    assert summary["auto_correlation_status"] != "completed"


def test_regeneration_clears_the_previous_validation(client):
    from app.api.deps import get_store
    response = client.post("/api/v1/analyses", files=_files(scenarios.scenario_login_token))
    aid = response.json()["analysis_id"]
    client.post(f"/api/v1/analyses/{aid}/generate", json={})
    analysis = get_store().get(aid)
    analysis.validation_report = {"status": "validated", "samplers_total": 2}
    analysis.jmx_status = "validated"
    assert client.post(f"/api/v1/analyses/{aid}/generate", json={"loops": 2}).status_code == 200
    status = client.get(f"/api/v1/analyses/{aid}/validation").json()
    assert status["jmx_status"] == "generated"
    assert status["report"] is None


def test_inflight_validation_cannot_validate_a_replaced_plan(client, monkeypatch):
    from app.api.deps import get_store
    from app.domain.models import JMeterValidationReport
    from app.services import jmeter_runner
    aid = client.post("/api/v1/analyses", files=_files(scenarios.scenario_login_token)).json()["analysis_id"]
    client.post(f"/api/v1/analyses/{aid}/generate", json={})
    analysis = get_store().get(aid)

    def replace_during_validation(*args, **kwargs):
        analysis.invalidate_generated()
        return JMeterValidationReport(status="validated", executed=True)

    monkeypatch.setattr(jmeter_runner, "run_plan", replace_during_validation)
    response = client.post(f"/api/v1/analyses/{aid}/validate", json={})
    assert response.status_code == 409
    assert analysis.jmx_status is None
    assert analysis.validation_report is None


def test_manual_rule_validation_errors(client):
    r = client.post("/api/v1/analyses", files=_files(scenarios.scenario_login_token))
    aid = r.json()["analysis_id"]
    execs = client.get(f"/api/v1/analyses/{aid}/executions").json()["items"]
    login_id = execs[0]["id"]
    profile_id = execs[1]["id"]

    # Invalid: value not present in the producer response.
    bad = client.post(f"/api/v1/analyses/{aid}/rules", json={
        "variable_name": "ghost",
        "producer": {
            "execution_id": login_id, "side": "response", "location_type": "json_body",
            "canonical_path": "$.nonexistent", "raw_value": "nope",
        },
        "consumers": [],
        "extractor_method": "json_path",
        "extractor_expression": "$.nonexistent",
    })
    assert bad.status_code == 422

    # Valid manual rule: extract token, consume in profile Authorization header.
    good = client.post(f"/api/v1/analyses/{aid}/rules", json={
        "variable_name": "authToken",
        "producer": {
            "execution_id": login_id, "side": "response", "location_type": "json_body",
            "canonical_path": "$.token", "raw_value": "tok_AAAA1111BBBB2222CCCC3333",
        },
        "consumers": [{
            "execution_id": profile_id, "side": "request", "location_type": "header",
            "canonical_path": "Authorization", "key": "Authorization",
            "raw_value": "Bearer tok_AAAA1111BBBB2222CCCC3333", "wrapper": "Bearer ",
        }],
        "extractor_method": "json_path",
        "extractor_expression": "$.token",
    })
    assert good.status_code == 201, good.text
    rule_id = good.json()["id"]

    val = client.post(f"/api/v1/analyses/{aid}/rules/{rule_id}/validate").json()
    assert val["valid"] is True

    # delete
    d = client.delete(f"/api/v1/analyses/{aid}/rules/{rule_id}")
    assert d.status_code == 204


def test_dependency_graph_endpoint(client):
    r = client.post("/api/v1/analyses", files=_files(scenarios.scenario_login_token))
    aid = r.json()["analysis_id"]

    # Before accepting anything, the graph already shows detected candidate edges.
    g = client.get(f"/api/v1/analyses/{aid}/graph")
    assert g.status_code == 200, g.text
    graph = g.json()
    assert graph["stats"]["apis"] == 2
    assert len(graph["nodes"]) == 2
    assert len(graph["edges"]) >= 1
    # every edge points from an earlier producer to a later consumer node
    idx = {n["id"]: n["index"] for n in graph["nodes"]}
    for e in graph["edges"]:
        assert idx[e["source"]] < idx[e["target"]]
        assert e["confidence"] in ("high", "medium", "low")
        assert e["variable"] in graph["facets"]["variables"]
    assert graph["facets"]["confidence_levels"]  # at least one level present

    # After accepting the high-confidence candidate, its edge is marked accepted.
    client.post(f"/api/v1/analyses/{aid}/candidates/accept-high")
    graph2 = client.get(f"/api/v1/analyses/{aid}/graph").json()
    assert any(e["state"] == "accepted" for e in graph2["edges"])
    producer_nodes = [n for n in graph2["nodes"] if n["role"] in ("producer", "both")]
    consumer_nodes = [n for n in graph2["nodes"] if n["role"] in ("consumer", "both")]
    assert producer_nodes and consumer_nodes


def test_auto_correlate_idempotent_and_materialized(client):
    r = client.post("/api/v1/analyses", files=_files(_webhook_scenario))
    summary = r.json()
    aid = summary["analysis_id"]
    assert summary["auto_correlation_status"] == "not_started"

    first = client.post(f"/api/v1/analyses/{aid}/auto-correlate").json()
    assert first["created"] >= 2  # merchantGUID + merchantID
    assert "merchantGUID" in first["variables"]
    assert "merchantID" in first["variables"]
    after = client.get(f"/api/v1/analyses/{aid}").json()
    assert after["auto_correlation_status"] == "completed"
    rule_count = after["rule_count"]

    # Second click is idempotent: same result, no duplicate rules.
    second = client.post(f"/api/v1/analyses/{aid}/auto-correlate").json()
    assert second.get("idempotent") is True
    assert client.get(f"/api/v1/analyses/{aid}").json()["rule_count"] == rule_count

    # Generate + download: the ${vars} must be materialized in the requests.
    gen = client.post(f"/api/v1/analyses/{aid}/generate", json={}).json()
    assert gen["status"] == "generated", gen
    jmx = client.get(f"/api/v1/analyses/{aid}/download/jmx").text
    assert "${merchantGUID}" in jmx           # placeholder/query/json-body consumers
    assert "${merchantID}" in jmx             # independent variable
    assert '"${merchantGUID}"' in jmx         # JSON body: quoted string preserved
    # webhook IDs have no producer, so they are never correlated:
    assert "${webhookID}" not in jmx and "${webhookId}" not in jmx
    assert "4585463" in jmx and "5353" in jmx  # left literal


def test_selected_merchant_later_in_response_array(client):
    def scenario(role):
        data = _webhook_scenario(role)
        merchants = [{"merchantID": i + 1000, "merchantGUID": f"unused-guid-{i:04d}"}
                     for i in range(106)]
        merchants.append({"merchantID": 6, "merchantGUID": _GUID})
        data["run"]["executions"][0]["response"]["stream"] = b.buffer_body(json.dumps(merchants))
        return data

    r = client.post("/api/v1/analyses", files=_files(scenario))
    aid = r.json()["analysis_id"]
    result = client.post(f"/api/v1/analyses/{aid}/auto-correlate").json()
    rules = {rule["variable_name"]: rule for rule in result["rules"]}
    assert rules["merchantID"]["producer"]["canonical_path"] == "$[106].merchantID"
    assert {c["execution_index"] for c in rules["merchantGUID"]["consumers"]} == {2, 3, 4}
    graph = client.get(f"/api/v1/analyses/{aid}/graph").json()
    assert graph["stats"]["edges"] == 4


def test_single_run_mode(client):
    b = json.dumps(scenarios.scenario_login_token("baseline")).encode()
    r = client.post("/api/v1/analyses", files=[("files", ("only.json", b, "application/json"))])
    assert r.status_code == 201
    assert r.json()["mode"] == "single_run"


def test_accepting_twice_then_auto_preserves_one_rule_even_after_rename(client):
    summary = client.post("/api/v1/analyses", files=_files(scenarios.scenario_login_token)).json()
    aid = summary["analysis_id"]
    candidate = client.get(f"/api/v1/analyses/{aid}/candidates").json()["items"][0]
    url = f"/api/v1/analyses/{aid}/candidates/{candidate['id']}/accept"
    first = client.post(url).json()
    second = client.post(url).json()
    assert first["id"] == second["id"]
    assert client.patch(f"/api/v1/analyses/{aid}/rules/{first['id']}", json={"variable_name": "session_token"}).status_code == 200
    auto = client.post(f"/api/v1/analyses/{aid}/auto-correlate").json()
    assert auto["total"] == 1 and auto["variables"] == ["session_token"]
    assert client.get(f"/api/v1/analyses/{aid}/graph").json()["stats"]["edges"] == 1


def test_invalid_edit_is_atomic_and_valid_edit_invalidates_download(client):
    aid = client.post("/api/v1/analyses", files=_files(scenarios.scenario_login_token)).json()["analysis_id"]
    rule = client.post(f"/api/v1/analyses/{aid}/auto-correlate").json()["rules"][0]
    assert client.post(f"/api/v1/analyses/{aid}/generate", json={}).status_code == 200
    url = f"/api/v1/analyses/{aid}/rules/{rule['id']}"
    assert client.patch(url, json={"extractor_expression": "$.missing"}).status_code == 422
    assert client.get(f"/api/v1/analyses/{aid}/download/jmx").status_code == 200
    assert client.patch(url, json={"variable_name": "fresh_token"}).status_code == 200
    assert client.get(f"/api/v1/analyses/{aid}/download/jmx").status_code != 200
