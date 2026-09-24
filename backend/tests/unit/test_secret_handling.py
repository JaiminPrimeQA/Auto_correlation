"""Secret-handling regression tests (fix #2 / #8).

A real secret, a masked secret, or a placeholder must NEVER be written into the
executable JMX. External credentials are referenced via ${__P(name,)} and
supplied at runtime with -Jname=<secret>. Runtime/transport headers are filtered.
"""

from __future__ import annotations

from app.core.config import Settings
from app.services.jmx_builder import BuildOptions, JmxBuilder
from app.services.normalizer import normalize_run
from tests.fixtures import builders as b

_SECRET = "73e4da84-ba06-4431-9787-d62f852565ca"


def _build_xml():
    run = normalize_run(
        b.report("Demo", [
            b.execution(
                "Call", "POST", "https://api.test/x",
                req_headers=[
                    b.header("Authorization", "Bearer TOPSECRET-abc.def.ghi"),
                    b.header("x-tokenguid", _SECRET),      # sensitive key -> property
                    b.header("Postman-Token", "runtime-noise-123"),  # filtered
                    b.header("Content-Length", "42"),      # filtered
                    b.header("Accept-Encoding", "gzip"),   # filtered
                    b.header("Content-Type", "application/json"),  # kept
                    b.header("merchantsGuid", _SECRET),    # GUID under innocuous key -> NOT externalised
                ],
                req_body=b.raw_json_body({"a": 1}),
                resp_body={"ok": True},
            ),
        ]),
        filename="d.json", settings=Settings(),
    )
    return JmxBuilder(BuildOptions()).build(run, [])


def test_secret_never_appears_in_plan():
    res = _build_xml()
    assert "Bearer TOPSECRET" not in res.xml
    assert "TOPSECRET-abc.def.ghi" not in res.xml


def test_no_masked_or_placeholder_value_in_plan():
    res = _build_xml()
    assert "__SET_ME__" not in res.xml
    assert "****" not in res.xml  # a masked value must never leak into the plan


def test_sensitive_key_uses_property_function():
    res = _build_xml()
    assert "${__P(x_tokenguid,)}" in res.xml
    assert "${__P(Authorization,)}" in res.xml
    assert "x_tokenguid" in res.required_properties
    assert "Authorization" in res.required_properties


def test_runtime_headers_are_filtered():
    res = _build_xml()
    lower = res.xml.lower()
    assert "postman-token" not in lower
    assert "content-length" not in lower
    assert "accept-encoding" not in lower
    # application headers are preserved
    assert "Content-Type" in res.xml


def test_zero_correlations_means_zero_extractors():
    res = _build_xml()
    assert res.extractor_counts == {}
    assert "JSONPostProcessor" not in res.xml
    assert "RegexExtractor" not in res.xml


def _build_with_user_agent(keep: bool):
    run = normalize_run(
        b.report("Demo", [
            b.execution(
                "Call", "GET", "https://api.test/x",
                req_headers=[b.header("User-Agent", "PostmanRuntime/7.0"),
                             b.header("Accept", "application/json")],
                resp_body={"ok": True},
            ),
        ]),
        filename="d.json", settings=Settings(),
    )
    return JmxBuilder(BuildOptions(keep_user_agent=keep)).build(run, [])


def test_user_agent_dropped_by_default_but_configurable():
    assert "PostmanRuntime" not in _build_with_user_agent(False).xml
    assert "PostmanRuntime" in _build_with_user_agent(True).xml
