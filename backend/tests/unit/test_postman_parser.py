import json

import pytest

from app.core.config import Settings
from app.core.errors import ProblemException
from app.services.postman_parser import parse_collection, parse_environment
from tests.fixtures.postman_builders import pm_collection, pm_environment, pm_request


def _raw(obj) -> bytes:
    return json.dumps(obj).encode()


def test_valid_collection_parses_without_warnings():
    collection = pm_collection("Demo", [pm_request("Ping", "GET", "https://api.example.com/ping")])
    parsed = parse_collection(_raw(collection), filename="c.json", settings=Settings())
    assert parsed.data["info"]["name"] == "Demo"
    assert parsed.warnings == []


def test_collection_missing_info_name_rejected():
    with pytest.raises(ProblemException) as exc:
        parse_collection(_raw({"item": []}), filename="c.json", settings=Settings())
    assert exc.value.errors[0]["path"] == "$.info.name"


def test_collection_missing_item_array_rejected():
    with pytest.raises(ProblemException) as exc:
        parse_collection(_raw({"info": {"name": "x"}}), filename="c.json", settings=Settings())
    assert exc.value.errors[0]["path"] == "$.item"


def test_collection_unknown_schema_warns_but_parses():
    collection = pm_collection("Demo", [], schema="")
    parsed = parse_collection(_raw(collection), filename="c.json", settings=Settings())
    assert any("schema version" in w for w in parsed.warnings)


def test_collection_empty_items_warns():
    collection = pm_collection("Demo", [])
    parsed = parse_collection(_raw(collection), filename="c.json", settings=Settings())
    assert any("no requests" in w for w in parsed.warnings)


def test_collection_oversize_rejected():
    with pytest.raises(ProblemException) as exc:
        parse_collection(b"x" * 100, filename="c.json", settings=Settings(max_collection_bytes=10))
    assert exc.value.status == 413


def test_valid_environment_parses_enabled_values_only():
    env = pm_environment("dev", {"host": "api.example.com", "token": "abc"}, disabled={"token"})
    parsed = parse_environment(_raw(env), filename="e.json", settings=Settings())
    assert parsed.values == {"host": "api.example.com"}
    assert parsed.warnings == []


def test_environment_missing_values_array_rejected():
    with pytest.raises(ProblemException) as exc:
        parse_environment(_raw({"name": "dev"}), filename="e.json", settings=Settings())
    assert exc.value.errors[0]["path"] == "$.values"


def test_environment_with_no_enabled_values_warns():
    env = pm_environment("dev", {"token": "abc"}, disabled={"token"})
    parsed = parse_environment(_raw(env), filename="e.json", settings=Settings())
    assert any("no enabled values" in w for w in parsed.warnings)


def test_environment_oversize_rejected():
    with pytest.raises(ProblemException) as exc:
        parse_environment(b"x" * 100, filename="e.json", settings=Settings(max_environment_bytes=10))
    assert exc.value.status == 413


def test_environment_entry_with_list_typed_key_is_skipped_not_crashed():
    # A malformed environment could have a non-string (here: list) "key", which
    # would otherwise raise TypeError: unhashable type: 'list' when used as a
    # dict key. It must be skipped like any other invalid entry instead.
    env = {"name": "dev", "values": [
        {"key": ["not", "a", "string"], "value": "x", "enabled": True},
        {"key": "host", "value": "api.example.com", "enabled": True},
    ]}
    parsed = parse_environment(_raw(env), filename="e.json", settings=Settings())
    assert parsed.values == {"host": "api.example.com"}
