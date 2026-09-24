# backend/tests/unit/test_postman_domain_extractor.py
from app.core.config import Settings
from app.services.postman_domain_extractor import extract_target_domains
from tests.fixtures.postman_builders import pm_collection, pm_request


def test_resolves_domain_from_environment_substituted_ip_literal():
    request = pm_request("Ping", "GET", "https://{{host}}/ping")
    report = extract_target_domains(
        pm_collection("Demo", [request]), environment_values={"host": "93.184.216.34"}, settings=Settings(),
    )
    assert report.resolved_domains == ["93.184.216.34"]
    assert report.warnings == []


def test_static_host_needs_no_environment():
    request = pm_request("Ping", "GET", "https://93.184.216.34/ping")
    report = extract_target_domains(pm_collection("Demo", [request]), environment_values={}, settings=Settings())
    assert report.resolved_domains == ["93.184.216.34"]


def test_unresolved_host_variable_produces_a_warning_not_an_exception():
    request = pm_request("Ping", "GET", "https://{{host}}/ping")
    report = extract_target_domains(pm_collection("Demo", [request]), environment_values={}, settings=Settings())
    assert report.resolved_domains == []
    assert "host" in report.warnings[0]
    assert "Ping" in report.warnings[0]


def test_blocked_destination_produces_a_warning_not_an_exception():
    request = pm_request("Ping", "GET", "https://localhost/ping")
    report = extract_target_domains(pm_collection("Demo", [request]), environment_values={}, settings=Settings())
    assert report.resolved_domains == []
    assert any("blocked" in w.lower() for w in report.warnings)


def test_dns_rebinding_hostname_is_reported_via_injected_resolver():
    request = pm_request("Ping", "GET", "https://evil.example.com/ping")

    def fake_resolver(hostname: str) -> list[str]:
        return ["127.0.0.1"]

    report = extract_target_domains(
        pm_collection("Demo", [request]), environment_values={}, settings=Settings(), resolver=fake_resolver,
    )
    assert report.resolved_domains == []
    assert any("loopback" in w for w in report.warnings)


def test_deduplicates_and_sorts_resolved_domains():
    requests = [pm_request("A", "GET", "https://93.184.216.34/a"), pm_request("B", "GET", "https://93.184.216.34/b")]
    report = extract_target_domains(pm_collection("Demo", requests), environment_values={}, settings=Settings())
    assert report.resolved_domains == ["93.184.216.34"]
