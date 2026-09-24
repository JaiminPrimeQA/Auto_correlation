import uuid

from app.core.config import Settings
from app.domain.enums import RuleOrigin, RuleState
from app.domain.models import CorrelationRule
from app.services.correlation_engine import CorrelationEngine
from app.services.jmx_builder import JmxBuilder
from app.services.jmx_validator import validate_jmx
from app.services.normalizer import normalize_run
from app.services.run_aligner import align_runs
from tests.fixtures import scenarios


def _rules_for(scenario):
    s = Settings()
    base = normalize_run(scenario("baseline"), filename="b.json", settings=s)
    comp = normalize_run(scenario("comparison"), filename="c.json", settings=s)
    cands = CorrelationEngine(s).analyze_two_run(base, comp, align_runs(base, comp))
    rules = [
        CorrelationRule(
            id=uuid.uuid4().hex,
            variable_name=c.variable_name,
            origin=RuleOrigin.AUTOMATIC,
            state=RuleState.ENABLED,
            producer=c.producer,
            consumers=list(c.consumers),
            extractor_method=c.extractor_method,
            extractor_expression=c.extractor_expression,
        )
        for c in cands
    ]
    return base, rules


def test_json_extractor_and_bearer_replacement():
    base, rules = _rules_for(scenarios.scenario_login_token)
    result = JmxBuilder().build(base, rules)
    xml = result.xml
    assert "JSONPostProcessor" in xml
    assert "JSONPostProcessor.jsonPathExprs" in xml
    # Authorization header now references the variable, keeping the Bearer wrapper.
    assert "Bearer ${token}" in xml or "Bearer ${" in xml
    assert result.replaced_consumers >= 1
    assert rules[0].producer.raw_value not in xml  # no stale token in user-defined variables


def test_runtime_correlation_variables_are_not_predeclared_as_not_found():
    """Extractors create correlation variables at runtime; a UDV sentinel is misleading."""
    base, rules = _rules_for(scenarios.scenario_record_id)
    xml = JmxBuilder().build(base, rules).xml
    # The extractor may retain a failure sentinel, but User Defined Variables
    # must contain only configuration such as BASE_HOST / BASE_PROTOCOL.
    udv_end = xml.index("</Arguments>", xml.index('testname="User Defined Variables"'))
    udv = xml[:udv_end]
    assert "__NOT_FOUND__" not in udv
    assert not any(f'<stringProp name="Argument.name">{r.variable_name}</stringProp>' in udv for r in rules)


def test_conflicting_rules_cannot_both_be_reported_as_materialized():
    base, rules = _rules_for(scenarios.scenario_login_token)
    duplicate = rules[0].model_copy(deep=True)
    duplicate.id = "conflict"
    duplicate.variable_name = "other_token"
    result = JmxBuilder().build(base, [rules[0], duplicate])
    assert result.unmaterialized
    assert any(x["variable"] == rules[0].variable_name for x in result.unmaterialized)


def test_filtered_jsonpath_and_match_number_are_validated():
    from app.domain.enums import ExtractorMethod
    from app.services.rule_validator import resolve_extractor
    from tests.fixtures import builders as b

    run = normalize_run(b.report("Demo", [b.execution("List", "GET", "http://local/list",
        resp_body=[{"merchantID": 5, "value": "first"}, {"merchantID": 6, "value": "second"}])]),
        filename="test.json", settings=Settings())
    source = run.executions[0]
    assert resolve_extractor(source, ExtractorMethod.JSON_PATH, "$[?(@.merchantID == 6)].value") == "second"
    assert resolve_extractor(source, ExtractorMethod.JSON_PATH, "$[*].value", 2) == "second"
    assert resolve_extractor(source, ExtractorMethod.JSON_PATH, "$[*].value", 3) is None


def test_generated_jmx_is_structurally_valid():
    base, rules = _rules_for(scenarios.scenario_record_id)
    result = JmxBuilder().build(base, rules)
    v = validate_jmx(result.xml)
    assert v.ok, v.errors
    # every used variable is defined by an extractor
    assert set(result.variables).issubset(set(v.defined_variables))


def test_json_body_numeric_and_string_substitution_valid_json():

    base, rules = _rules_for(scenarios.scenario_record_id)
    result = JmxBuilder().build(base, rules)
    # The "Add Note" body references ${orderId} and must remain valid JSON once resolved.
    assert "${orderId}" in result.xml


def test_xml_escaping_of_values():
    from tests.fixtures.builders import execution, header, raw_json_body, report

    s = Settings()
    ex = execution(
        "X", "POST", "https://api.example.com/x",
        req_headers=[header("X-Note", "a<b>&\"c\"")],
        req_body=raw_json_body({"v": "a<b>"}),
        resp_body={"ok": True},
    )
    base = normalize_run(report("C", [ex]), filename="b.json", settings=s)
    result = JmxBuilder().build(base, [])
    # raw '<' must not appear unescaped inside text; ElementTree escapes it
    assert "a<b>" not in result.xml
    assert "a&lt;b&gt;" in result.xml
    assert validate_jmx(result.xml).ok


def test_undefined_variable_detected_by_validator():
    # Hand-craft a plan referencing an undefined variable.
    bad = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3"><hashTree>'
        '<TestPlan testclass="TestPlan" testname="t"><stringProp name="p">${undef}</stringProp></TestPlan>'
        "<hashTree/></hashTree></jmeterTestPlan>"
    )
    v = validate_jmx(bad)
    assert not v.ok
    assert any("undef" in e for e in v.errors)


def test_regex_escaping_in_header_extractor():
    # Header producer -> regex extractor with escaped header name.
    from tests.fixtures.builders import execution, header, report

    s = Settings()
    base = normalize_run(report("C", [
        execution("Get", "GET", "https://api.example.com/a",
                  resp_headers=[header("Content-Type", "application/json"),
                                header("X-Auth.Token", "val_ABCDEF123456")],
                  resp_body={"ok": True}, position=0),
        execution("Use", "GET", "https://api.example.com/b",
                  req_headers=[header("X-Custom", "val_ABCDEF123456")],
                  resp_body={"ok": True}, position=1),
    ]), filename="b.json", settings=s)
    comp = normalize_run(report("C", [
        execution("Get", "GET", "https://api.example.com/a",
                  resp_headers=[header("Content-Type", "application/json"),
                                header("X-Auth.Token", "val_ZZZZZZ999999")],
                  resp_body={"ok": True}, position=0),
        execution("Use", "GET", "https://api.example.com/b",
                  req_headers=[header("X-Custom", "val_ZZZZZZ999999")],
                  resp_body={"ok": True}, position=1),
    ]), filename="c.json", settings=s)
    cands = CorrelationEngine(s).analyze_two_run(base, comp, align_runs(base, comp))
    assert cands
    # The regex expression should have regex metacharacters in the header name escaped.
    expr = cands[0].extractor_expression
    assert "\\.Token" in expr  # the '.' is escaped
