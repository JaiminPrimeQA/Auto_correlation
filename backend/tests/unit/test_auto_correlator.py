"""One-click auto-correlation by value threading."""

from __future__ import annotations

from app.core.config import Settings
from app.services import auto_correlator
from app.services.normalizer import normalize_run
from tests.fixtures import builders as b


def _run(execs):
    return normalize_run(b.report("Demo", execs), filename="d.json", settings=Settings())


def test_correlates_every_reused_value_thread():
    run = _run([
        b.execution("Login", "POST", "https://api.test/login", req_body=b.raw_json_body({"u": "a"}),
                    resp_body={"token": "TOK-abcdef1234"}, position=0),
        b.execution("List", "GET", "https://api.test/list",
                    resp_body={"sessionRef": "SESS-99887766"}, position=1),
        b.execution("Data", "GET", "https://api.test/data",
                    req_headers=[b.header("Authorization", "Bearer TOK-abcdef1234")], position=2),
        b.execution("Act", "POST", "https://api.test/act",
                    req_body=b.raw_json_body({"sessionRef": "SESS-99887766"}), position=3),
    ])
    rules = auto_correlator.auto_correlate(run, Settings())
    produced = {r.producer.canonical_path for r in rules}
    assert "$.token" in produced        # token → Authorization (Bearer)
    assert "$.sessionRef" in produced   # sessionRef → later body
    total_consumers = sum(len(r.consumers) for r in rules)
    assert total_consumers == 2


def test_skips_value_that_is_only_an_input():
    run = _run([
        b.execution("A", "GET", "https://api.test/a", resp_body={"unused": "NOPE-1234567"}, position=0),
        b.execution("B", "GET", "https://api.test/b?x=OTHER-7654321", resp_body={"ok": True}, position=1),
    ])
    assert auto_correlate_paths(run) == set()


def test_skips_ambiguous_value_appearing_twice_in_response():
    run = _run([
        b.execution("List", "GET", "https://api.test/list",
                    resp_body={"a": {"gid": "DUP-value-123"}, "b": {"gid": "DUP-value-123"}}, position=0),
        b.execution("Use", "GET", "https://api.test/use?gid=DUP-value-123", position=1),
    ])
    # value appears twice in the producer response → ambiguous → not auto-correlated
    assert auto_correlate_paths(run) == set()


def auto_correlate_paths(run):
    return {r.producer.canonical_path for r in auto_correlator.auto_correlate(run, Settings())}
