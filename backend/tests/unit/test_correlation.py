from app.core.config import Settings
from app.domain.enums import Confidence, LocationType
from app.services.correlation_engine import CorrelationEngine
from app.services.normalizer import normalize_run
from app.services.run_aligner import align_runs
from tests.fixtures import scenarios


def _pair(scenario):
    s = Settings()
    base = normalize_run(scenario("baseline"), filename="b.json", settings=s)
    comp = normalize_run(scenario("comparison"), filename="c.json", settings=s)
    alignment = align_runs(base, comp)
    return base, comp, alignment, s


def test_login_token_detected_high_confidence():
    base, comp, alignment, s = _pair(scenarios.scenario_login_token)
    cands = CorrelationEngine(s).analyze_two_run(base, comp, alignment)
    assert cands, "expected at least one candidate"
    token = next(c for c in cands if c.producer.canonical_path == "$.token")
    assert token.confidence == Confidence.HIGH
    # consumer is the Authorization header, wrapped with Bearer
    con = token.consumers[0]
    assert con.location_type == LocationType.HEADER
    assert con.wrapper and con.wrapper.strip().lower() == "bearer"


def test_record_id_used_in_path_query_and_body():
    base, comp, alignment, s = _pair(scenarios.scenario_record_id)
    cands = CorrelationEngine(s).analyze_two_run(base, comp, alignment)
    order = next(c for c in cands if c.producer.canonical_path == "$.orderId")
    locs = {c.location_type for c in order.consumers}
    assert LocationType.PATH in locs
    assert LocationType.QUERY in locs
    assert LocationType.JSON_BODY in locs


def test_later_response_echo_is_not_a_second_producer():
    base, comp, alignment, s = _pair(scenarios.scenario_echoed_record_id)
    cands = CorrelationEngine(s).analyze_two_run(base, comp, alignment)

    assert len(cands) == 1
    assert cands[0].producer.execution_index == 0
    assert cands[0].producer.canonical_path == "$.id"
    assert len(cands[0].consumers) == 3


def test_static_value_not_correlated():
    base, comp, alignment, s = _pair(scenarios.scenario_static_false_positive)
    cands = CorrelationEngine(s).analyze_two_run(base, comp, alignment)
    # tenant is identical across runs -> condition 3 (values differ) fails
    assert all(c.producer.canonical_path != "$.tenant" for c in cands)


def test_producer_must_precede_consumer():
    # In the login scenario the producer (index 0) precedes the consumer (index 1).
    base, comp, alignment, s = _pair(scenarios.scenario_login_token)
    cands = CorrelationEngine(s).analyze_two_run(base, comp, alignment)
    for c in cands:
        for con in c.consumers:
            assert con.execution_index > c.producer.execution_index


def test_csrf_from_html_text_body():
    base, comp, alignment, s = _pair(scenarios.scenario_csrf_html)
    cands = CorrelationEngine(s).analyze_two_run(base, comp, alignment)
    # csrf appears in HTML (text) producer and is posted downstream
    assert any("csrf" in c.variable_name.lower() for c in cands)


def test_single_run_lower_confidence():
    s = Settings()
    run = normalize_run(scenarios.scenario_login_token("baseline"), filename="b.json", settings=s)
    cands = CorrelationEngine(s).analyze_single_run(run)
    assert cands
    assert all(c.confidence != Confidence.HIGH for c in cands)
    assert all(any("Single-run" in w for w in c.warnings) for c in cands)
