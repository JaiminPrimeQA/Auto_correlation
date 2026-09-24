"""Classification taxonomy regression tests.

A changed value alone is never a correlation. These tests pin the six buckets:
CORRELATION / COOKIE_MANAGED / EXTERNAL_CREDENTIAL / PARAMETERIZATION /
REVIEW_REQUIRED / NOISE.
"""

from __future__ import annotations

from app.core.config import Settings
from app.domain.enums import Classification
from app.services import analysis_service
from tests.fixtures import builders as b


def _two_run_analysis():
    def executions(cookie_value: str, user_id: str):
        return [
            b.execution(
                "Login",
                "POST",
                "https://api.example.test/login",
                req_headers=[
                    b.header("x-api-key", "SUPERSECRETKEY123456"),  # sensitive key -> credential
                    b.header("Postman-Token", f"tok-{cookie_value}"),  # runtime noise
                    b.header("Content-Type", "application/json"),
                ],
                req_body=b.raw_json_body({"placeholder": "string"}),  # -> review_required
                resp_headers=[
                    b.header("Content-Type", "application/json"),
                    b.header("Set-Cookie", f"sid={cookie_value}; Path=/"),
                ],
                resp_body={"ok": True},
                position=0,
            ),
            b.execution(
                "GetData",
                "GET",
                f"https://api.example.test/data?userId={user_id}",
                req_headers=[
                    b.header("x-api-key", "SUPERSECRETKEY123456"),
                    b.header("Postman-Token", f"tok2-{cookie_value}"),
                    b.header("Cookie", f"sid={cookie_value}"),  # cookie-managed replay
                ],
                resp_headers=[b.header("Content-Type", "application/json")],
                resp_body={"rows": [1, 2, 3]},
                position=1,
            ),
        ]

    run_a = b.to_bytes(b.report("Demo", executions("AAA111", "100")))
    run_b = b.to_bytes(b.report("Demo", executions("BBB222", "200")))
    return analysis_service.build_analysis(
        [("run-a.json", run_a), ("run-b.json", run_b)], Settings()
    )


def _by_class(insights, cls: Classification):
    return [i for i in insights if i.classification == cls]


def test_no_fabricated_correlation():
    a = _two_run_analysis()
    # The only cross-run dynamic values are a managed cookie and a param; neither
    # is a proven response->request correlation.
    assert a.summary.correlations == 0
    assert len(a.candidates) == 0


def test_cookie_is_cookie_managed_not_a_regex_extractor():
    a = _two_run_analysis()
    cookies = _by_class(a.insights, Classification.COOKIE_MANAGED)
    assert len(cookies) == 1
    assert a.summary.cookie_managed == 1
    assert "Cookie Manager" in cookies[0].recommended_handling


def test_sensitive_key_is_external_credential():
    a = _two_run_analysis()
    creds = _by_class(a.insights, Classification.EXTERNAL_CREDENTIAL)
    assert a.summary.external_credentials == 1
    assert creds[0].location.key.lower() == "x-api-key"


def test_changed_request_value_without_producer_is_parameterization():
    a = _two_run_analysis()
    params = _by_class(a.insights, Classification.PARAMETERIZATION)
    assert a.summary.parameterizations == 1
    assert params[0].differs_across_runs is True
    assert params[0].location.key == "userId"


def test_placeholder_value_is_review_required():
    a = _two_run_analysis()
    reviews = _by_class(a.insights, Classification.REVIEW_REQUIRED)
    assert any(r.location.key == "placeholder" or "$.placeholder" in r.location.canonical_path for r in reviews)
    assert a.summary.review_required >= 1


def test_runtime_headers_are_noise():
    a = _two_run_analysis()
    noise = _by_class(a.insights, Classification.NOISE)
    keys = {n.location.key.lower() for n in noise}
    assert "postman-token" in keys
    assert a.summary.noise >= 1


def test_correlated_sensitive_header_is_not_double_counted_as_credential():
    """A token carried in an Authorization header is a correlation consumer;
    it must NOT also be counted as an external credential."""
    def execs(tok):
        return [
            b.execution("Login", "POST", "https://api.example.test/login",
                        req_body=b.raw_json_body({"u": "a"}),
                        resp_body={"token": tok, "ok": True}, position=0),
            b.execution("GetData", "GET", "https://api.example.test/data",
                        req_headers=[b.header("Authorization", "Bearer " + tok)],
                        resp_body={"ok": True}, position=1),
        ]
    a = analysis_service.build_analysis(
        [("a.json", b.to_bytes(b.report("Demo", execs("AAA11122")))),
         ("b.json", b.to_bytes(b.report("Demo", execs("BBB33344"))))],
        Settings(),
    )
    assert a.summary.correlations == 1
    assert a.summary.external_credentials == 0
