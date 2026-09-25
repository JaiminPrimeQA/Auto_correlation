from app.core.config import Settings
from app.services.normalizer import normalize_run
from app.services.run_aligner import align_runs
from tests.fixtures.builders import execution, report
from tests.fixtures.scenarios import scenario_repeated_request


def _norm(rep):
    return normalize_run(rep, filename="f.json", settings=Settings())


def test_repeated_requests_align_by_occurrence():
    base = _norm(scenario_repeated_request("baseline"))
    comp = _norm(scenario_repeated_request("comparison"))
    report_ = align_runs(base, comp)
    assert report_.matched == 4
    assert report_.coverage == 1.0


def test_missing_request_detected():
    a = _norm(report("C", [
        execution("A", "GET", "https://x.com/a", resp_body={"ok": 1}, position=0),
        execution("B", "GET", "https://x.com/b", resp_body={"ok": 1}, position=1),
    ]))
    b = _norm(report("C", [
        execution("A", "GET", "https://x.com/a", resp_body={"ok": 1}, position=0),
    ]))
    r = align_runs(a, b)
    assert r.matched == 1
    assert r.missing == 1
    assert r.coverage == 0.5


def test_dynamic_path_segment_normalized_for_alignment():
    a = _norm(report("C", [
        execution("Get", "GET", "https://x.com/orders/11112222", resp_body={"ok": 1}),
    ]))
    b = _norm(report("C", [
        execution("Get", "GET", "https://x.com/orders/99998888", resp_body={"ok": 1}),
    ]))
    r = align_runs(a, b)
    assert r.matched == 1  # long numeric id normalised to {id}


def test_short_numeric_path_id_normalized_for_alignment():
    # Restful-Booker style: a new 4-digit booking id per run in the URL path.
    a = _norm(report("C", [
        execution("Get booking", "GET", "https://x.com/booking/2361", resp_body={"ok": 1}),
        execution("Delete booking", "DELETE", "https://x.com/booking/2361", resp_body={"ok": 1}),
    ]))
    b = _norm(report("C", [
        execution("Get booking", "GET", "https://x.com/booking/2362", resp_body={"ok": 1}),
        execution("Delete booking", "DELETE", "https://x.com/booking/2362", resp_body={"ok": 1}),
    ]))
    r = align_runs(a, b)
    assert r.matched == 2
    assert r.coverage == 1.0
