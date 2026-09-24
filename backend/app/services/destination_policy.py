"""Public-HTTPS-only destination policy.

Blocks loopback, RFC1918/link-local/unique-local/multicast/unspecified/reserved
IP ranges and cloud-metadata hosts, in both literal-IP and DNS-resolved form.
This module makes no network calls itself except through the injectable
resolver in `validate_destination` (Task 3), which callers can replace with a
fake in tests to avoid depending on real DNS.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

from ..core.config import Settings
from ..core.errors import blocked_destination, validation_error

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


Resolver = Callable[[str], list[str]]


def default_resolver(hostname: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError as exc:
        raise validation_error(f"Could not resolve host '{hostname}'.") from exc
    return sorted({info[4][0] for info in infos})


def validate_destination(url: str, *, settings: Settings, resolver: Resolver | None = None) -> None:
    resolve = resolver or default_resolver
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise blocked_destination(f"Unsupported protocol '{scheme or '(none)'}'; only HTTPS is permitted.")
    if scheme == "http" and settings.https_only:
        raise blocked_destination("Plain HTTP destinations are not permitted; use HTTPS.")

    host = parts.hostname
    if not host:
        raise validation_error("URL has no hostname.")
    if is_blocked_hostname(host, settings):
        raise blocked_destination(f"Destination host '{host}' is blocked.")

    if is_ip_literal(host):
        reason = classify_ip(host)
        if reason:
            raise blocked_destination(f"Destination IP is {reason.replace('_', ' ')}; not permitted.")
        return

    for ip_str in resolve(host):
        reason = classify_ip(ip_str) if is_ip_literal(ip_str) else None
        if reason:
            raise blocked_destination(
                f"Destination host '{host}' resolves to a {reason.replace('_', ' ')} address; not permitted."
            )
