"""Robust array selectors: fixed indices become order-independent filters."""

from __future__ import annotations

from app.services.jmx_builder import _robustify_jsonpath


def test_sibling_extractors_use_same_stable_id_when_guid_changes_and_rows_move():
    from app.core.config import Settings
    from app.services.auto_correlator import auto_correlate
    from app.services.jmx_builder import JmxBuilder
    from app.services.normalizer import normalize_run
    from tests.fixtures import builders as b

    def run(guid, reverse):
        merchants = [{"merchantID": 5, "merchantGUID": "unused-guid"}, {"merchantID": 6, "merchantGUID": guid}]
        if reverse:
            merchants.reverse()
        return normalize_run(b.report("Demo", [
            b.execution("List", "GET", "http://local/merchants", resp_body=merchants, position=0),
            b.execution("Use", "POST", "http://local/report", position=1,
                        req_body=b.raw_json_body({"MerchantID": "6", "merchantsGuid": guid})),
        ]), filename=f"{reverse}.json", settings=Settings())

    base, comparison = run("guid-baseline-1234", False), run("guid-comparison-9876", True)
    rules = auto_correlate(base, Settings())
    result = JmxBuilder().build(base, rules, comparison)
    assert not result.errors
    assert set(result.extractor_expressions.values()) == {
        "$[?(@.merchantID==6)].merchantID", "$[?(@.merchantID==6)].merchantGUID",
    }
    assert "guid-baseline-1234" not in result.xml


def test_dynamic_anchor_is_not_baked_into_filter():
    body = [{"merchantID": 1234, "merchantGUID": "old-guid"}]
    other = [{"merchantID": 9876, "merchantGUID": "new-guid"}]
    assert _robustify_jsonpath("$[0].merchantGUID", body, allow_target=True,
                              comparison_body=other, require_comparison=True) == "$[0].merchantGUID"


def test_top_level_array_index_becomes_id_filter():
    body = [
        {"merchantID": 5, "merchantGUID": "g5", "name": "A"},
        {"merchantID": 6, "merchantGUID": "g6", "name": "B"},
    ]
    assert _robustify_jsonpath("$[1].merchantGUID", body) == "$[?(@.merchantID==6)].merchantGUID"


def test_string_anchor_is_quoted():
    body = [{"name": "Acme Ltd", "token": "t1"}, {"name": "Other", "token": "t2"}]
    # no id-like key -> falls back to a unique scalar (name)
    assert _robustify_jsonpath("$[0].token", body) == "$[?(@.name=='Acme Ltd')].token"


def test_nested_array_path():
    body = {"data": {"items": [{"id": 10, "code": "x"}, {"id": 11, "code": "y"}]}}
    assert _robustify_jsonpath("$.data.items[1].code", body) == "$.data.items[?(@.id==11)].code"


def test_non_array_path_unchanged():
    assert _robustify_jsonpath("$.token", {"token": "abc"}) == "$.token"


def test_kept_when_no_unique_anchor():
    # every sibling value repeats -> cannot build a unique filter -> keep index
    body = [{"gid": "same", "kind": "x"}, {"gid": "same", "kind": "x"}]
    assert _robustify_jsonpath("$[0].kind", body) == "$[0].kind"


def test_kept_when_body_missing():
    assert _robustify_jsonpath("$[3].merchantGUID", None) == "$[3].merchantGUID"
