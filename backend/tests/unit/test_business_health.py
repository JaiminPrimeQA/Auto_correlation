"""Business/scenario health + readiness gating regression tests.

HTTP 2xx is never treated as proof of a successful business flow.
"""

from __future__ import annotations

from app.core.config import Settings
from app.domain.enums import ReadinessState
from app.services import analysis_service
from app.services.business_health import (
    detect_scenario_inconsistencies,
    scan_business_signals,
)
from app.services.normalizer import normalize_run
from tests.fixtures import builders as b


def _normalized(execs):
    return normalize_run(b.report("Demo", execs), filename="demo.json", settings=Settings())


def test_business_signals_behind_http_200():
    run = _normalized([
        b.execution(
            "History", "GET", "https://api.test/history", resp_code=200,
            resp_body={"isSuccess": False, "message": "Unrecognized Guid format.", "statusCode": -3},
        ),
        b.execution(
            "Update", "PATCH", "https://api.test/update", resp_code=200,
            resp_body={"m_Code": -61, "m_Text": "Access denied"},
        ),
        b.execution(
            "Report", "POST", "https://api.test/report", resp_code=200,
            resp_body={"totalRecords": 0, "rows": []},
        ),
    ])
    signals, empty = scan_business_signals(run)
    kinds = {s.kind for s in signals}
    assert "success_false" in kinds
    assert "negative_status" in kinds
    assert "access_denied" in kinds
    assert "empty_dataset" in kinds
    assert empty == 1


def test_failed_assertion_is_a_business_signal():
    run = _normalized([
        b.execution(
            "Events", "GET", "https://api.test/events", resp_code=200, resp_body=[1, 2],
            assertions=[b.failed_assertion("Body matches string", "expected X to include Y")],
        ),
    ])
    signals, _ = scan_business_signals(run)
    assert any(s.kind == "assertion_failed" for s in signals)


def test_clean_run_has_no_business_signals():
    run = _normalized([
        b.execution("OK", "GET", "https://api.test/ok", resp_code=200,
                    resp_body={"isSuccess": True, "statusCode": 0, "data": {"id": 1}}),
    ])
    signals, empty = scan_business_signals(run)
    assert signals == []
    assert empty == 0


def test_scenario_inconsistency_flagged():
    def execs(count: int):
        return [
            b.execution("Report", "GET", "https://api.test/report?merchant=fixed", resp_code=200,
                        resp_body={"totalRecords": count, "rows": list(range(count))}),
        ]
    a = _normalized(execs(206))
    c = _normalized(execs(0))
    from app.services import run_aligner
    alignment = run_aligner.align_runs(a, c)
    warnings = detect_scenario_inconsistencies(a, c, alignment)
    assert len(warnings) == 1
    assert "may not represent the same business flow" in warnings[0]


def test_readiness_downgraded_by_business_errors_despite_all_2xx():
    a = analysis_service.build_analysis(
        [
            ("a.json", b.to_bytes(b.report("Demo", [
                b.execution("History", "GET", "https://api.test/history", resp_code=200,
                            resp_body={"isSuccess": False, "message": "bad", "statusCode": -3}),
            ]))),
            ("b.json", b.to_bytes(b.report("Demo", [
                b.execution("History", "GET", "https://api.test/history", resp_code=200,
                            resp_body={"isSuccess": False, "message": "bad", "statusCode": -3}),
            ]))),
        ],
        Settings(),
    )
    # every response is HTTP 200, yet the pair is not READY.
    assert a.readiness.state == ReadinessState.READY_WITH_REVIEW
    assert any("business-error" in r for r in a.readiness.reasons)


def test_readiness_not_ready_on_transport_blocker():
    def execs():
        return [b.execution("Auth", "GET", "https://api.test/x", resp_code=401, resp_status="Unauthorized",
                            resp_body={"error": "no"})]
    a = analysis_service.build_analysis(
        [("a.json", b.to_bytes(b.report("Demo", execs()))),
         ("b.json", b.to_bytes(b.report("Demo", execs())))],
        Settings(),
    )
    assert a.readiness.state == ReadinessState.NOT_READY


def test_readiness_ready_when_clean():
    def execs():
        return [b.execution("OK", "GET", "https://api.test/ok", resp_code=200,
                            resp_body={"isSuccess": True, "statusCode": 0, "data": {"id": 1}})]
    a = analysis_service.build_analysis(
        [("a.json", b.to_bytes(b.report("Demo", execs()))),
         ("b.json", b.to_bytes(b.report("Demo", execs())))],
        Settings(),
    )
    assert a.readiness.state == ReadinessState.READY
    assert a.readiness.reasons == []
