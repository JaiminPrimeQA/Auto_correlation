import pytest

from app.core.config import Settings
from app.core.errors import ProblemException
from app.utils.json_safety import check_json_complexity, decode_json


def test_decode_json_tolerates_bom():
    raw = ("﻿" + '{"a": 1}').encode("utf-8")
    assert decode_json(raw) == {"a": 1}


def test_decode_json_rejects_invalid_json_with_path():
    with pytest.raises(ProblemException) as exc:
        decode_json(b"{not json")
    assert exc.value.errors[0]["path"] == "$"


def test_decode_json_rejects_non_utf8_bytes_cleanly():
    # A UTF-16-encoded upload (or any non-UTF-8 byte stream) must raise a clean
    # ProblemException, not an unhandled UnicodeDecodeError.
    raw = '{"a": 1}'.encode("utf-16")
    with pytest.raises(ProblemException) as exc:
        decode_json(raw)
    assert exc.value.code == "validation_error"
    assert exc.value.errors[0]["path"] == "$"


def test_check_json_complexity_rejects_deep_nesting():
    settings = Settings(max_json_depth=2)
    with pytest.raises(ProblemException) as exc:
        check_json_complexity({"a": {"b": {"c": 1}}}, settings)
    assert "Depth exceeds limit" in exc.value.errors[0]["detail"]


def test_check_json_complexity_rejects_oversized_array():
    settings = Settings(max_array_length=2)
    with pytest.raises(ProblemException):
        check_json_complexity([1, 2, 3], settings)


def test_check_json_complexity_allows_within_bounds():
    check_json_complexity({"a": [1, 2, 3]}, Settings())  # no exception
