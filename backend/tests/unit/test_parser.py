import json

import pytest

from app.core.config import Settings
from app.core.errors import ProblemException
from app.services.newman_parser import parse_report
from tests.fixtures.scenarios import scenario_login_token


def test_valid_report_parses():
    raw = json.dumps(scenario_login_token("baseline")).encode()
    parsed = parse_report(raw, filename="baseline.json", settings=Settings())
    assert parsed.data["run"]["executions"]


def test_invalid_json_raises_path_error():
    with pytest.raises(ProblemException) as exc:
        parse_report(b"{not json", filename="x.json", settings=Settings())
    assert exc.value.code == "validation_error"
    assert exc.value.errors[0]["path"] == "$"


def test_unrelated_json_rejected():
    with pytest.raises(ProblemException) as exc:
        parse_report(b'{"foo": "bar"}', filename="x.json", settings=Settings())
    assert exc.value.errors[0]["path"] == "$.run"


def test_empty_executions_rejected():
    raw = json.dumps({"run": {"executions": []}}).encode()
    with pytest.raises(ProblemException) as exc:
        parse_report(raw, filename="x.json", settings=Settings())
    assert "$.run.executions" in exc.value.errors[0]["path"]


def test_bom_tolerated():
    raw = ("﻿" + json.dumps(scenario_login_token("baseline"))).encode("utf-8")
    parsed = parse_report(raw, filename="x.json", settings=Settings())
    assert parsed.data["run"]["executions"]


def test_oversize_rejected():
    s = Settings(max_file_bytes=10)
    with pytest.raises(ProblemException) as exc:
        parse_report(b"x" * 100, filename="x.json", settings=s)
    assert exc.value.status == 413


def test_depth_limit():
    s = Settings(max_json_depth=3)
    # deeply nested but wrapped in run.executions to pass structure order
    nested = {"a": {"b": {"c": {"d": 1}}}}
    doc = {"run": {"executions": [{"request": {"method": "GET"}}]}, "extra": nested}
    with pytest.raises(ProblemException):
        parse_report(json.dumps(doc).encode(), filename="x.json", settings=s)
