from app.core.config import Settings
from app.domain.enums import BodyMode
from app.services.normalizer import normalize_run
from tests.fixtures.builders import execution, header, raw_json_body, report


def test_url_reconstruction_and_query_duplicates():
    ex = execution(
        "Dup", "GET", "https://api.example.com/search?q=a&q=b&sort=asc",
        resp_body={"ok": True},
    )
    run = normalize_run(report("C", [ex]), filename="f.json", settings=Settings())
    req = run.executions[0].request
    assert req.host == "api.example.com"
    assert req.path == "/search"
    # duplicate query keys preserved in order
    names = [(p.name, p.value) for p in req.query]
    assert names == [("q", "a"), ("q", "b"), ("sort", "asc")]


def test_json_body_parsed():
    ex = execution(
        "Post", "POST", "https://api.example.com/x",
        req_headers=[header("Content-Type", "application/json")],
        req_body=raw_json_body({"a": 1, "b": {"c": "d"}}),
        resp_body={"ok": True},
    )
    run = normalize_run(report("C", [ex]), filename="f.json", settings=Settings())
    req = run.executions[0].request
    assert req.body_mode == BodyMode.JSON
    assert req.parsed_body == {"a": 1, "b": {"c": "d"}}


def test_duplicate_headers_preserved():
    ex = execution(
        "H", "GET", "https://api.example.com/x",
        req_headers=[header("X-Multi", "1"), header("X-Multi", "2")],
        resp_body={"ok": True},
    )
    run = normalize_run(report("C", [ex]), filename="f.json", settings=Settings())
    multi = [p.value for p in run.executions[0].request.headers if p.name == "X-Multi"]
    assert multi == ["1", "2"]


def test_response_buffer_decoded_to_json():
    ex = execution("R", "GET", "https://api.example.com/x", resp_body={"token": "abc"})
    run = normalize_run(report("C", [ex]), filename="f.json", settings=Settings())
    resp = run.executions[0].response
    assert resp.parsed_body == {"token": "abc"}
    assert resp.code == 200
