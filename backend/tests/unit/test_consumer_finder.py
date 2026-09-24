"""Assisted correlation: find where a response value is reused downstream."""

from __future__ import annotations

from app.core.config import Settings
from app.services import consumer_finder
from app.services.normalizer import normalize_run
from tests.fixtures import builders as b


def _run(execs):
    return normalize_run(b.report("Demo", execs), filename="d.json", settings=Settings())


def _find(run, exec_id, loc, path):
    prod_exec, src = consumer_finder.find_producer_source(run, exec_id, loc, path)
    return consumer_finder.find_consumers(run, prod_exec, src)


def test_finds_json_body_producer_consumed_in_header_with_bearer_wrapper():
    run = _run([
        b.execution("Login", "POST", "https://api.test/login", req_body=b.raw_json_body({"u": "a"}),
                    resp_body={"token": "TOK-abcdef123"}, position=0),
        b.execution("Data", "GET", "https://api.test/data",
                    req_headers=[b.header("Authorization", "Bearer TOK-abcdef123")],
                    resp_body={"ok": True}, position=1),
    ])
    login = next(e for e in run.executions if e.item_name == "Login")
    matches = _find(run, login.id, "json_body", "$.token")
    assert len(matches) == 1
    e, sink, wrapper = matches[0]
    assert e.item_name == "Data"
    assert sink.key == "Authorization"
    assert wrapper == "Bearer "


def test_finds_response_header_producer():
    run = _run([
        b.execution("Csrf", "GET", "https://api.test/csrf", resp_code=200,
                    resp_headers=[b.header("Content-Type", "application/json"),
                                  b.header("x-csrf-token", "CSRF-9k2mfd")],
                    resp_body={"ok": True}, position=0),
        b.execution("Post", "POST", "https://api.test/act", req_body=b.raw_json_body({"csrf": "CSRF-9k2mfd"}),
                    resp_body={"ok": True}, position=1),
    ])
    csrf = next(e for e in run.executions if e.item_name == "Csrf")
    matches = _find(run, csrf.id, "header", "x-csrf-token")
    assert len(matches) == 1
    assert matches[0][0].item_name == "Post"


def test_no_consumers_when_value_not_reused():
    run = _run([
        b.execution("A", "GET", "https://api.test/a", resp_body={"token": "UNIQUE-xyz789"}, position=0),
        b.execution("B", "GET", "https://api.test/b", resp_body={"ok": True}, position=1),
    ])
    a = next(e for e in run.executions if e.item_name == "A")
    assert _find(run, a.id, "json_body", "$.token") == []


def test_name_match_finds_renamed_field_with_different_value():
    # Producer returns merchantGUID; a later request has merchantsGuid with a
    # DIFFERENT (hardcoded) value. Value-match misses it; name-match catches it.
    run = _run([
        b.execution("List", "GET", "https://api.test/list", resp_code=200,
                    resp_body={"items": [{"merchantGUID": "REAL-6d0999-guid"}]}, position=0),
        b.execution("Update", "POST", "https://api.test/update",
                    req_body=b.raw_json_body({"merchantsGuid": "HARDCODED-0f0310"}), position=1),
    ])
    lst = next(e for e in run.executions if e.item_name == "List")
    prod_exec, src = consumer_finder.find_producer_source(run, lst.id, "json_body", "$.items[0].merchantGUID")
    # exact-value match finds nothing
    assert consumer_finder.find_consumers(run, prod_exec, src) == []
    # name match finds the renamed field
    nm = consumer_finder.find_name_matches(run, prod_exec, src)
    assert len(nm) == 1
    e, sink, producer_field, consumer_field = nm[0]
    assert sink.canonical_path == "$.merchantsGuid"
    assert producer_field == "merchantGUID" and consumer_field == "merchantsGuid"


def test_name_match_skips_generic_short_names():
    run = _run([
        b.execution("A", "GET", "https://api.test/a", resp_body={"id": 111}, position=0),
        b.execution("B", "GET", "https://api.test/b?id=222", resp_body={"ok": True}, position=1),
    ])
    a = next(e for e in run.executions if e.item_name == "A")
    prod_exec, src = consumer_finder.find_producer_source(run, a.id, "json_body", "$.id")
    assert consumer_finder.find_name_matches(run, prod_exec, src) == []  # "id" is too generic


def test_within_one_edit():
    assert consumer_finder._within_one_edit("merchantguid", "merchantsguid") is True
    assert consumer_finder._within_one_edit("webhookid", "webhookid") is True
    assert consumer_finder._within_one_edit("token", "session") is False


def test_only_searches_later_requests():
    # A value that appears in an EARLIER request must not be reported (producer
    # must precede consumer).
    run = _run([
        b.execution("Early", "GET", "https://api.test/early?x=SHARED-val999", resp_body={"ok": True}, position=0),
        b.execution("Prod", "GET", "https://api.test/prod", resp_body={"v": "SHARED-val999"}, position=1),
    ])
    prod = next(e for e in run.executions if e.item_name == "Prod")
    assert _find(run, prod.id, "json_body", "$.v") == []
