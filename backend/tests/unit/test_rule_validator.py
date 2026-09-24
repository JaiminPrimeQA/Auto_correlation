"""Manual-rule validation messages, especially the actionable ambiguous-source
guidance for values that repeat across array rows."""

from __future__ import annotations

import uuid

from app.core.config import Settings
from app.domain.enums import ExtractorMethod, RuleOrigin, RuleState
from app.domain.models import CorrelationRule
from app.services import rule_validator
from app.services.normalizer import normalize_run
from app.services.value_indexer import index_request_sinks, index_response_sources
from tests.fixtures import builders as b


def _run(items_count: int):
    rows = [{"gid": "SAMEVAL-abc"} for _ in range(items_count)]
    return normalize_run(
        b.report("Demo", [
            b.execution("Report", "GET", "https://api.test/report", resp_code=200,
                        resp_body={"items": rows}, position=0),
            b.execution("ById", "GET", "https://api.test/byid?gid=SAMEVAL-abc", resp_code=200,
                        resp_body={"ok": True}, position=1),
        ]),
        filename="d.json", settings=Settings(),
    )


def _ambiguous_rule(run):
    prod = next(e for e in run.executions if e.item_name == "Report")
    cons = next(e for e in run.executions if e.item_name == "ById")
    psrc = next(s for s in index_response_sources(prod) if s.canonical_path == "$.items[1].gid")
    csink = next(s for s in index_request_sinks(cons) if s.key == "gid")
    return CorrelationRule(
        id=uuid.uuid4().hex, variable_name="gid", origin=RuleOrigin.MANUAL, state=RuleState.ENABLED,
        producer=psrc, consumers=[csink], extractor_method=ExtractorMethod.JSON_PATH,
        extractor_expression="$.items[1].gid",
    )


def test_ambiguous_array_message_is_actionable():
    run = _run(3)
    issues = rule_validator.validate_rule(_ambiguous_rule(run), run, run, set())
    amb = next(i for i in issues if i.code == "ambiguous_producers")
    assert "3 rows of a repeating array" in amb.detail
    assert "User Defined Variable" in amb.detail
    assert "[1]" in amb.detail  # names the fragile fixed index


def test_ambiguous_message_flags_absence_in_comparison_run():
    run_a = _run(3)          # producer array has rows
    run_b = _run(0)          # producer array empty in the other run
    issues = rule_validator.validate_rule(_ambiguous_rule(run_a), run_a, run_a, set(), comparison_run=run_b)
    amb = next(i for i in issues if i.code == "ambiguous_producers")
    assert "absent/empty in the comparison run" in amb.detail


def test_no_absence_note_when_value_present_in_comparison():
    run_a = _run(3)
    run_b = _run(3)          # present in both runs
    issues = rule_validator.validate_rule(_ambiguous_rule(run_a), run_a, run_a, set(), comparison_run=run_b)
    amb = next(i for i in issues if i.code == "ambiguous_producers")
    assert "absent/empty in the comparison run" not in amb.detail
