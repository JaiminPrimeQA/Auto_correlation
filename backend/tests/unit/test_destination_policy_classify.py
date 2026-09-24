import pytest

from app.core.config import Settings
from app.services.destination_policy import (
    classify_ip,
    extract_hostname,
    is_blocked_hostname,
    is_ip_literal,
)


@pytest.mark.parametrize("ip,expected", [
    ("127.0.0.1", "loopback"),
    ("10.1.2.3", "private"),
    ("172.16.0.5", "private"),
    ("192.168.1.1", "private"),
    ("169.254.169.254", "link_local"),
    ("224.0.0.1", "multicast"),
    ("0.0.0.0", "unspecified"),
    ("8.8.8.8", None),
    ("93.184.216.34", None),
    ("::1", "loopback"),
    ("fe80::1", "link_local"),
    ("fc00::1", "private"),
    ("2001:4860:4860::8888", None),
])
def test_classify_ip(ip, expected):
    assert classify_ip(ip) == expected


def test_is_ip_literal():
    assert is_ip_literal("127.0.0.1") is True
    assert is_ip_literal("::1") is True
    assert is_ip_literal("api.example.com") is False


def test_is_blocked_hostname_matches_localhost_variants_and_metadata():
    s = Settings()
    assert is_blocked_hostname("localhost", s) is True
    assert is_blocked_hostname("sub.localhost", s) is True
    assert is_blocked_hostname("LOCALHOST", s) is True
    assert is_blocked_hostname("169.254.169.254", s) is True
    assert is_blocked_hostname("metadata.google.internal", s) is True
    assert is_blocked_hostname("api.example.com", s) is False


def test_extract_hostname_handles_plain_and_ipv6():
    assert extract_hostname("https://api.example.com:443/x") == "api.example.com"
    assert extract_hostname("https://[::1]:8080/x") == "::1"
    assert extract_hostname("not a url") is None
