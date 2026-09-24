from app.core.config import Settings
from app.domain.enums import LocationType
from app.services.normalizer import normalize_run
from app.services.value_indexer import (
    flatten_json,
    index_request_sinks,
    index_response_sources,
    occurrence_transforms,
)
from tests.fixtures.builders import execution, header, raw_json_body, report


def test_flatten_nested_and_arrays():
    table = dict(flatten_json({"a": {"b": [10, {"c": "x"}]}}))
    assert table["$.a.b[0]"] == 10
    assert table["$.a.b[1].c"] == "x"


def test_response_sources_include_json_and_headers():
    ex = execution(
        "R", "GET", "https://api.example.com/x",
        resp_headers=[header("Content-Type", "application/json"), header("X-Token", "hdr123")],
        resp_body={"id": 42, "nested": {"token": "abc"}},
    )
    run = normalize_run(report("C", [ex]), filename="f.json", settings=Settings())
    srcs = index_response_sources(run.executions[0])
    paths = {(s.location_type, s.canonical_path) for s in srcs}
    assert (LocationType.JSON_BODY, "$.nested.token") in paths
    assert (LocationType.HEADER, "X-Token") in paths


def test_request_sinks_cover_locations():
    ex = execution(
        "R", "POST", "https://api.example.com/orders/abc?ref=xyz",
        req_headers=[header("Authorization", "Bearer tkn")],
        req_body=raw_json_body({"orderId": "abc"}),
        resp_body={"ok": True},
    )
    run = normalize_run(report("C", [ex]), filename="f.json", settings=Settings())
    sinks = index_request_sinks(run.executions[0])
    kinds = {s.location_type for s in sinks}
    assert LocationType.PATH in kinds
    assert LocationType.QUERY in kinds
    assert LocationType.HEADER in kinds
    assert LocationType.JSON_BODY in kinds


def test_transforms_include_url_and_json_escape():
    labels = {t[1] for t in occurrence_transforms('a b"c')}
    assert "raw" in labels
    assert "url_encode" in labels
    assert "json_escape" in labels
