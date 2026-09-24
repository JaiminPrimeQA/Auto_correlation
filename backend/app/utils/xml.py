"""XML helpers for JMX generation and safe parsing of untrusted XML bodies."""

from __future__ import annotations

import xml.etree.ElementTree as ET

# Safe parser for UNTRUSTED response bodies (XSS/XXE hardened).
try:  # pragma: no cover - import guard
    from defusedxml.ElementTree import fromstring as _safe_fromstring
except Exception:  # pragma: no cover
    _safe_fromstring = None


def safe_parse_xml(text: str):
    """Parse untrusted XML with entity/DTD expansion disabled.

    Returns an Element or None if parsing fails or defusedxml is unavailable.
    """
    if not text or _safe_fromstring is None:
        return None
    try:
        return _safe_fromstring(text)
    except Exception:
        return None


def indent(elem: ET.Element, level: int = 0) -> None:
    """In-place pretty-print indentation (stdlib ET.indent shim for clarity)."""
    ET.indent(elem, space="  ", level=level)


def to_string(root: ET.Element) -> str:
    """Serialise a JMX tree with the standard XML declaration JMeter expects."""
    indent(root)
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"
