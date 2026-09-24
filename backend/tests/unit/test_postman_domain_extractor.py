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


def test_memoizes_validation_per_unique_host_within_one_inspection():
    # Two requests to the same rebinding host must only trigger one resolver
    # call - the blocking DNS lookup must not repeat per occurrence (Important #1).
    calls: list[str] = []

    def counting_resolver(hostname: str) -> list[str]:
        calls.append(hostname)
        return ["127.0.0.1"]  # rebinds to loopback -> blocked

    requests = [
        pm_request("A", "GET", "https://evil.example.com/a"),
        pm_request("B", "GET", "https://evil.example.com/b"),
    ]
    report = extract_target_domains(
        pm_collection("Demo", requests), environment_values={}, settings=Settings(), resolver=counting_resolver,
    )
    assert calls == ["evil.example.com"]
    assert len(report.warnings) == 2
    assert all("loopback" in w for w in report.warnings)


def test_caps_distinct_hosts_validated_and_warns_once():
    settings = Settings(max_target_hosts_to_validate=2)
    calls: list[str] = []

    def counting_resolver(hostname: str) -> list[str]:
        calls.append(hostname)
        return ["93.184.216.34"]

    requests = [
        pm_request("A", "GET", "https://host-a.example.com/a"),
        pm_request("B", "GET", "https://host-b.example.com/b"),
        pm_request("C", "GET", "https://host-c.example.com/c"),
        pm_request("D", "GET", "https://host-d.example.com/d"),
    ]
    report = extract_target_domains(
        pm_collection("Demo", requests), environment_values={}, settings=settings, resolver=counting_resolver,
    )
    assert calls == ["host-a.example.com", "host-b.example.com"]
    assert report.resolved_domains == ["host-a.example.com", "host-b.example.com"]
    cap_warnings = [w for w in report.warnings if "stopped after 2 distinct hosts" in w]
    assert len(cap_warnings) == 1
