import pytest

from app.core.config import Settings
from app.core.errors import ProblemException
from app.services.destination_policy import validate_destination


def test_allows_public_https_literal_ip():
    validate_destination("https://93.184.216.34/health", settings=Settings())  # no exception


def test_rejects_plain_http_by_default():
    with pytest.raises(ProblemException) as exc:
        validate_destination("http://93.184.216.34/health", settings=Settings())
    assert exc.value.code == "blocked_destination"


def test_allows_http_when_explicitly_permitted_in_development():
    validate_destination("http://93.184.216.34/health", settings=Settings(allow_insecure_http_destinations=True))


def test_rejects_http_in_production_even_if_flag_set():
    with pytest.raises(ProblemException):
        validate_destination(
            "http://93.184.216.34/health",
            settings=Settings(environment="production", allow_insecure_http_destinations=True),
        )


def test_rejects_non_http_scheme():
    with pytest.raises(ProblemException) as exc:
        validate_destination("ws://93.184.216.34/socket", settings=Settings())
    assert exc.value.code == "blocked_destination"


def test_rejects_loopback_literal():
    with pytest.raises(ProblemException) as exc:
        validate_destination("https://127.0.0.1/admin", settings=Settings())
    assert "loopback" in exc.value.detail


def test_rejects_blocked_hostname_without_dns():
    with pytest.raises(ProblemException):
        validate_destination("https://localhost/admin", settings=Settings())


def test_rejects_hostname_resolving_to_private_range_dns_rebinding():
    def fake_resolver(hostname: str) -> list[str]:
        assert hostname == "evil.example.com"
        return ["10.0.0.5"]

    with pytest.raises(ProblemException) as exc:
        validate_destination("https://evil.example.com/", settings=Settings(), resolver=fake_resolver)
    assert "private" in exc.value.detail


def test_allows_hostname_resolving_to_public_ip_only():
    def fake_resolver(hostname: str) -> list[str]:
        return ["93.184.216.34"]

    validate_destination("https://api.example.com/", settings=Settings(), resolver=fake_resolver)


def test_rejects_if_any_resolved_ip_is_blocked():
    def fake_resolver(hostname: str) -> list[str]:
        return ["93.184.216.34", "127.0.0.1"]  # one public, one blocked -> reject

    with pytest.raises(ProblemException):
        validate_destination("https://multi.example.com/", settings=Settings(), resolver=fake_resolver)


def test_rejects_url_with_no_hostname():
    with pytest.raises(ProblemException) as exc:
        validate_destination("https:///no-host", settings=Settings())
    assert exc.value.code == "validation_error"
