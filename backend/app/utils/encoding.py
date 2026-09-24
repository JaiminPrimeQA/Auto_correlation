"""Safe decoding of Newman response bodies.

Newman represents bodies in several shapes:
  * response.stream = {"type": "Buffer", "data": [.. ints ..]}
  * response.stream = [.. ints ..]
  * response.stream = "string"
  * response.body   = "string"
  * absent / empty

Byte arrays are decoded strictly as UTF-8; on failure we record a warning and
fall back to a lossy representation while retaining the exact original bytes
length. We never execute or fetch anything found in a body.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DecodedBody:
    text: str
    warnings: list[str] = field(default_factory=list)
    byte_length: int = 0


def _bytes_from_int_list(data: list) -> bytes | None:
    try:
        return bytes(int(b) & 0xFF for b in data)
    except (TypeError, ValueError):
        return None


def decode_body(stream: object, body: object, *, max_bytes: int) -> DecodedBody:
    """Return decoded text for a Newman execution response.

    `stream` and `body` are the raw values from the Newman execution response.
    """
    warnings: list[str] = []

    # 1. Buffer object form
    if isinstance(stream, dict) and stream.get("type") == "Buffer":
        data = stream.get("data")
        if isinstance(data, list):
            return _decode_int_list(data, max_bytes, warnings)

    # 2. Raw int-array form
    if isinstance(stream, list):
        return _decode_int_list(stream, max_bytes, warnings)

    # 3. String stream
    if isinstance(stream, str):
        return DecodedBody(text=stream, byte_length=len(stream.encode("utf-8", "replace")))

    # 4. response.body variants
    if isinstance(body, str):
        return DecodedBody(text=body, byte_length=len(body.encode("utf-8", "replace")))
    if isinstance(body, (dict, list)):
        # Some custom reporters embed the already-parsed JSON body.
        import json

        try:
            text = json.dumps(body, ensure_ascii=False)
            return DecodedBody(text=text, byte_length=len(text.encode("utf-8")))
        except (TypeError, ValueError):
            warnings.append("Could not serialise structured body; ignored.")

    return DecodedBody(text="", warnings=warnings, byte_length=0)


def _decode_int_list(data: list, max_bytes: int, warnings: list[str]) -> DecodedBody:
    if len(data) > max_bytes:
        warnings.append(
            f"Response body exceeds max buffer length ({len(data)} > {max_bytes}); truncated."
        )
        data = data[:max_bytes]
    raw = _bytes_from_int_list(data)
    if raw is None:
        warnings.append("Buffer contained non-byte values; body ignored.")
        return DecodedBody(text="", warnings=warnings, byte_length=0)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        warnings.append("Body was not valid UTF-8; decoded with replacement characters.")
        text = raw.decode("utf-8", errors="replace")
    return DecodedBody(text=text, warnings=warnings, byte_length=len(raw))
