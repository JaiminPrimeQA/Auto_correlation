from app.utils.encoding import decode_body


def test_buffer_int_array_decodes_utf8():
    stream = {"type": "Buffer", "data": list(b"hello")}
    result = decode_body(stream, None, max_bytes=1000)
    assert result.text == "hello"
    assert result.byte_length == 5
    assert result.warnings == []


def test_raw_int_list_stream():
    result = decode_body(list(b"{}"), None, max_bytes=1000)
    assert result.text == "{}"


def test_invalid_utf8_falls_back_with_warning():
    stream = {"type": "Buffer", "data": [0xFF, 0xFE, 0x41]}
    result = decode_body(stream, None, max_bytes=1000)
    assert "A" in result.text
    assert any("UTF-8" in w for w in result.warnings)


def test_string_body_variant():
    result = decode_body(None, "plain text", max_bytes=1000)
    assert result.text == "plain text"


def test_empty_body():
    result = decode_body(None, None, max_bytes=1000)
    assert result.text == ""
    assert result.byte_length == 0


def test_buffer_truncation_warns():
    data = list(b"x" * 100)
    result = decode_body({"type": "Buffer", "data": data}, None, max_bytes=10)
    assert len(result.text) == 10
    assert any("truncated" in w for w in result.warnings)
