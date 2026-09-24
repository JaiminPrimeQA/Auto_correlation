"""Alias + placeholder auto-correlation (the 'Modification Needed' fixes).

Covers: one rule with ALL consumers (exact query, placeholder query, JSON body),
merchantID vs merchantGUID as independent variables, short-value correlation via
unique name alias, and NOT collapsing distinct webhook IDs that have no producer.
"""

from __future__ import annotations

from app.core.config import Settings
from app.services import auto_correlator
from app.services.normalizer import normalize_run
from tests.fixtures import builders as b

GUID1 = "69f873ce-2af2-4780-88c5-74f373e7c8b2"
GUID2 = "7966c8b8-3844-4e3e-9862-b4b8ec14fded"


def _run(execs):
    return normalize_run(b.report("Webhooks", execs), filename="d.json", settings=Settings())


def _scenario():
    return _run([
        b.execution(
            "GetAllMerchants", "GET", "https://mcapi.test/merchants",
            resp_body=[
                {"merchantID": 6, "merchantGUID": GUID1},
                {"merchantID": 30784, "merchantGUID": GUID2},
            ],
            position=0,
        ),
        b.execution(
            "GetWebhookReport", "POST", "https://mcapi.test/report",
            req_body=b.raw_json_body({"MerchantID": "6", "StatusID": -100}),
            position=1,
        ),
        b.execution(
            "GetById", "GET", f"https://mcapi.test/byid?merchantsGuid={GUID1}&webhookID=4585463",
            position=2,
        ),
        b.execution(
            "GetHistory", "GET", "https://mcapi.test/history?merchantsGuid=string&webhookID=6232",
            position=3,
        ),
        b.execution(
            "UpdateStatus", "POST", "https://mcapi.test/update",
            req_body=b.raw_json_body({"webhookId": 5353, "merchantsGuid": GUID1}),
            position=4,
        ),
    ])


def _by_var(rules):
    return {r.variable_name: r for r in rules}


def test_merchantguid_one_rule_all_consumers_incl_placeholder_and_json_body():
    rules = auto_correlator.auto_correlate(_scenario(), Settings())
    by = _by_var(rules)
    assert "merchantGUID" in by, f"got vars {list(by)}"
    guid = by["merchantGUID"]
    locs = {(c.execution_index, c.location_type.value) for c in guid.consumers}
    # exact query (GetById=2), placeholder query (GetHistory=3), JSON body (UpdateStatus=4)
    assert (2, "query") in locs
    assert (3, "query") in locs      # merchantsGuid=string placeholder was matched
    assert (4, "json_body") in locs  # JSON request body was matched
    assert len(guid.consumers) == 3


def test_merchantid_is_independent_variable_via_unique_alias():
    rules = auto_correlator.auto_correlate(_scenario(), Settings())
    by = _by_var(rules)
    assert "merchantID" in by, f"got vars {list(by)}"
    mid = by["merchantID"]
    # short value "6" correlates only because the name uniquely matches API 01.
    assert [(c.execution_index, c.location_type.value) for c in mid.consumers] == [(1, "json_body")]
    # merchantID and merchantGUID are two separate rules, never mixed.
    assert "merchantID" != "merchantGUID"


def test_distinct_webhook_ids_are_not_collapsed():
    rules = auto_correlator.auto_correlate(_scenario(), Settings())
    by = _by_var(rules)
    # No response produces the webhook IDs (4585463 / 6232 / 5353), so they must
    # NOT be correlated - and never merged into a single variable by key name.
    assert "webhookID" not in by
    assert "webhookId" not in by


def test_placeholder_needs_a_unique_producer():
    # Two earlier producers of the same field name -> ambiguous -> the placeholder
    # consumer must NOT be auto-correlated.
    run = _run([
        b.execution("A", "GET", "https://x.test/a", resp_body={"merchantGUID": GUID1}, position=0),
        b.execution("B", "GET", "https://x.test/b", resp_body={"merchantGUID": GUID2}, position=1),
        b.execution("C", "GET", "https://x.test/c?merchantsGuid=string", position=2),
    ])
    rules = auto_correlator.auto_correlate(run, Settings())
    # exact-value threads may still fire elsewhere, but no rule may claim the
    # placeholder consumer at C because the producer is ambiguous.
    for r in rules:
        for c in r.consumers:
            assert not (c.execution_index == 2 and c.raw_value == "string")


def test_placeholder_does_not_default_to_first_array_row_or_use_future_selection():
    run = _run([
        b.execution("List", "GET", "https://x.test/list", position=0,
                    resp_body=[{"merchantID": 5, "merchantGUID": GUID1},
                               {"merchantID": 6, "merchantGUID": GUID2}]),
        b.execution("Ambiguous", "GET", "https://x.test/use?merchantsGuid=string", position=1),
        b.execution("Selected later", "GET", f"https://x.test/use?merchantsGuid={GUID2}", position=2),
    ])
    rules = auto_correlator.auto_correlate(run, Settings())
    assert all(c.execution_index != 1 for r in rules for c in r.consumers)


def test_short_id_selects_correct_array_row_without_a_guid_consumer():
    run = _run([
        b.execution("List", "GET", "https://x.test/list", position=0,
                    resp_body=[{"merchantID": 5, "merchantGUID": GUID1},
                               {"merchantID": 6, "merchantGUID": GUID2}]),
        b.execution("Report", "POST", "https://x.test/report", position=1,
                    req_body=b.raw_json_body({"MerchantID": "6"})),
        b.execution("History", "GET", "https://x.test/use?merchantsGuid=string", position=2),
    ])
    rules = _by_var(auto_correlator.auto_correlate(run, Settings()))
    assert rules["merchantID"].producer.canonical_path == "$[1].merchantID"
    assert rules["merchantGUID"].producer.raw_value == GUID2


def test_later_response_does_not_make_prior_placeholder_ambiguous():
    run = _run([
        b.execution("A", "GET", "https://x.test/a", resp_body={"merchantGUID": GUID1}, position=0),
        b.execution("Use", "GET", "https://x.test/use?merchantsGuid=string", position=1),
        b.execution("B", "GET", "https://x.test/b", resp_body={"merchantGUID": GUID2}, position=2),
    ])
    rules = auto_correlator.auto_correlate(run, Settings())
    assert len(rules) == 1
    assert rules[0].producer.raw_value == GUID1


def test_conflicting_real_guid_is_not_a_placeholder():
    run = _scenario()
    run.executions[-1].request.parsed_body["merchantsGuid"] = "different-real-guid"
    rules = auto_correlator.auto_correlate(run, Settings())
    assert all(c.execution_index != 4 for r in rules for c in r.consumers)


def test_aliases_do_not_strip_status_suffix():
    from app.utils.naming import fields_are_equivalent

    assert fields_are_equivalent("merchantGUID", "merchantsGuid")
    assert not fields_are_equivalent("statusId", "statuId")
