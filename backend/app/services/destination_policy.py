"""Public-HTTPS-only destination policy.

Blocks loopback, RFC1918/link-local/unique-local/multicast/unspecified/reserved
IP ranges and cloud-metadata hosts, in both literal-IP and DNS-resolved form.
This module makes no network calls itself except through the injectable
resolver in `validate_destination` (Task 3), which callers can replace with a
fake in tests to avoid depending on real DNS.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from ..core.config import Settings

_BLOCKED_IP_REASONS = ("is_loopback", "is_link_local", "is_multicast", "is_unspecified", "is_private", "is_reserved")
_REASON_NAMES = {
    "is_loopback": "loopback",
    "is_link_local": "link_local",
    "is_multicast": "multicast",
    "is_unspecified": "unspecified",
    "is_private": "private",
    "is_reserved": "reserved",
}


def is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def classify_ip(ip_str: str) -> str | None:
    """Return a blocked-category name for this IP literal, or None if it is a public address.

    Caller must ensure `ip_str` is a valid IP literal (check with `is_ip_literal` first).
    """
    ip = ipaddress.ip_address(ip_str)
    for attr in _BLOCKED_IP_REASONS:
        if getattr(ip, attr):
            return _REASON_NAMES[attr]
    return None


def is_blocked_hostname(hostname: str, settings: Settings) -> bool:
    h = hostname.strip().lower().rstrip(".")
    if h == "localhost" or h.endswith(".localhost"):
        return True
    return h in settings.blocked_hostname_list


def extract_hostname(url: str) -> str | None:
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None
