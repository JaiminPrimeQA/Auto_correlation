"""Secret detection and masking.

Secrets must never appear in logs and are masked in the UI by default. This
module provides pure functions used both for log redaction and API responses.
"""

from __future__ import annotations

import re

# Header / key names whose values are considered sensitive.
SENSITIVE_KEY_PATTERNS = [
    re.compile(r"authorization", re.I),
    re.compile(r"api[-_]?key", re.I),
    re.compile(r"x-api-key", re.I),
    re.compile(r"password", re.I),
    re.compile(r"passwd", re.I),
    re.compile(r"secret", re.I),
    re.compile(r"client[-_]?secret", re.I),
    re.compile(r"token", re.I),
    re.compile(r"access[-_]?token", re.I),
    re.compile(r"refresh[-_]?token", re.I),
    re.compile(r"session", re.I),
    re.compile(r"cookie", re.I),
    re.compile(r"set-cookie", re.I),
    re.compile(r"csrf", re.I),
    re.compile(r"xsrf", re.I),
]

# Value shapes that look like secrets even when the key is innocuous.
_JWT = re.compile(r"^[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}$")
_GUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_BEARER = re.compile(r"^Bearer\s+\S+", re.I)


def is_sensitive_key(key: str | None) -> bool:
    if not key:
        return False
    return any(p.search(key) for p in SENSITIVE_KEY_PATTERNS)


def looks_like_secret_value(value: str | None) -> bool:
    if not value:
        return False
    v = value.strip()
    if _JWT.match(v) or _GUID.match(v) or _BEARER.match(v):
        return True
    return False


def mask_value(value: str | None, *, keep: int = 4) -> str:
    """Mask a value keeping a short prefix/suffix for recognisability.

    Never returns the full secret. Empty and short values are fully masked.
    """
    if value is None:
        return ""
    v = str(value)
    if len(v) <= keep + 2:
        return "*" * len(v)
    visible = keep // 2
    return f"{v[:visible]}{'*' * (len(v) - 2 * visible)}{v[-visible:]}"


def mask_if_sensitive(key: str | None, value: str | None) -> str:
    if value is None:
        return ""
    if is_sensitive_key(key) or looks_like_secret_value(value):
        return mask_value(value)
    return value


def redact_for_log(text: str) -> str:
    """Redact obvious secrets from a string destined for logs."""
    if not text:
        return text
    text = re.sub(r"(?i)(authorization\s*[:=]\s*)(\S+)", r"\1[REDACTED]", text)
    text = _JWT.sub("[REDACTED_JWT]", text)
    text = re.sub(r"(?i)(bearer\s+)\S+", r"\1[REDACTED]", text)
    return text
