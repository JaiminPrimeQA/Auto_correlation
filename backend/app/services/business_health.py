"""Business / scenario health and correlation-readiness gating.

HTTP 2xx is not proof that a business flow succeeded. This module scans response
bodies for heuristic business-error indicators, detects scenario inconsistencies
across the two runs, and computes an explicit readiness state. It never blocks
correlation on a heuristic alone - the worst a heuristic does is downgrade a
pair to READY_WITH_REVIEW.
"""

from __future__ import annotations

from ..domain.enums import LocationType, ReadinessState
from ..domain.models import (
    AlignmentReport,
    BusinessSignal,
    ClassificationSummary,
    NormalizedExecution,
    NormalizedRun,
    ReadinessReport,
)
from .value_indexer import index_request_sinks

# Boolean flags whose False value indicates a failed business call.
_BOOL_SUCCESS_KEYS = {"issuccess", "success", "authresult", "ok", "succeeded", "isvalid"}
# Numeric status keys; a NEGATIVE value indicates failure in these APIs.
_STATUS_KEYS = {"statuscode", "status_code", "m_code", "resultcode", "errorcode", "returncode"}
# Keys carrying a human message.
_MESSAGE_KEYS = {"message", "m_text", "msg", "detail", "description", "error", "errormessage"}
# Keys that hold an error object/value when non-empty.
_ERROR_KEYS = {"error", "errors", "errormessage", "exception"}
# Phrases that indicate an authorization/access problem.
_DENIED_TERMS = ("access denied", "denied", "unauthorized", "unauthorised", "forbidden", "not authorized")
# Keys that carry a record count.
_COUNT_KEYS = {"totalrecords", "total", "count", "recordcount", "totalcount", "totalcnt"}


# --------------------------------------------------------------------------- #
# Business-signal scanning
# --------------------------------------------------------------------------- #

def scan_business_signals(run: NormalizedRun) -> tuple[list[BusinessSignal], int]:
    signals: list[BusinessSignal] = []
    empty_datasets = 0
    for e in run.executions:
        for a in e.assertions:
            if a.failed:
                signals.append(
                    BusinessSignal(
                        execution_id=e.id, execution_index=e.original_index, name=e.item_name,
                        kind="assertion_failed", severity="warning",
                        detail=a.error_message or a.name,
                    )
                )
        resp = e.response
        if resp is None or not resp.present:
            continue
        pb = resp.parsed_body
        if _is_empty_dataset(pb):
            empty_datasets += 1
            signals.append(
                BusinessSignal(
                    execution_id=e.id, execution_index=e.original_index, name=e.item_name,
                    kind="empty_dataset", severity="review",
                    detail="Response contains no records (possible empty producer dataset).",
                )
            )
        signals.extend(_scan_body_flags(e, pb))
    return signals, empty_datasets


def _scan_body_flags(e: NormalizedExecution, pb: object) -> list[BusinessSignal]:
    nodes: list[dict] = []
    if isinstance(pb, dict):
        nodes.append(pb)
        data = pb.get("data")
        if isinstance(data, dict):
            nodes.append(data)
    out: list[BusinessSignal] = []
    seen_kinds: set[str] = set()
    for node in nodes:
        message = _first_message(node)
        for k, v in node.items():
            lk = k.lower()
            if lk in _BOOL_SUCCESS_KEYS and v is False and "success_false" not in seen_kinds:
                seen_kinds.add("success_false")
                out.append(_sig(e, "success_false", f"{k}=false" + (f": {message}" if message else "")))
            elif lk in _STATUS_KEYS and isinstance(v, int) and not isinstance(v, bool) and v < 0 and "negative_status" not in seen_kinds:
                seen_kinds.add("negative_status")
                out.append(_sig(e, "negative_status", f"{k}={v}" + (f": {message}" if message else "")))
            elif lk in _ERROR_KEYS and v not in (None, "", [], {}) and "error_message" not in seen_kinds:
                seen_kinds.add("error_message")
                out.append(_sig(e, "error_message", f"{k}={_short(v)}"))
            if isinstance(v, str) and any(t in v.lower() for t in _DENIED_TERMS) and "access_denied" not in seen_kinds:
                seen_kinds.add("access_denied")
                out.append(_sig(e, "access_denied", f"{k}={_short(v)}"))
    return out


def _sig(e: NormalizedExecution, kind: str, detail: str) -> BusinessSignal:
    return BusinessSignal(
        execution_id=e.id, execution_index=e.original_index, name=e.item_name,
        kind=kind, severity="review", detail=detail,
    )


def _first_message(node: dict) -> str | None:
    for k, v in node.items():
        if k.lower() in _MESSAGE_KEYS and isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _short(v: object, limit: int = 120) -> str:
    s = str(v)
    return s if len(s) <= limit else s[:limit] + "..."


def _is_empty_dataset(pb: object) -> bool:
    if isinstance(pb, list):
        return len(pb) == 0
    if isinstance(pb, dict):
        for k, v in pb.items():
            if k.lower() in _COUNT_KEYS and isinstance(v, (int, float)) and not isinstance(v, bool):
                if v == 0:
                    return True
    return False


def _dataset_size(pb: object) -> int | None:
    """A comparable size metric for a response dataset, or None if not a dataset."""
    if isinstance(pb, list):
        return len(pb)
    if isinstance(pb, dict):
        for k, v in pb.items():
            if k.lower() in _COUNT_KEYS and isinstance(v, (int, float)) and not isinstance(v, bool):
                return int(v)
        lists = [len(v) for v in pb.values() if isinstance(v, list)]
        if lists:
            return max(lists)
    return None


# --------------------------------------------------------------------------- #
# Scenario consistency
# --------------------------------------------------------------------------- #

def detect_scenario_inconsistencies(
    baseline: NormalizedRun,
    comparison: NormalizedRun | None,
    alignment: AlignmentReport | None,
) -> list[str]:
    if comparison is None or alignment is None:
        return []
    comp_by_id = {e.id: e for e in comparison.executions}
    base_by_id = {e.id: e for e in baseline.executions}
    warnings: list[str] = []
    for pair in alignment.pairs:
        if pair.status != "matched":
            continue
        a = base_by_id.get(pair.baseline_execution_id or "")
        b = comp_by_id.get(pair.comparison_execution_id or "")
        if a is None or b is None or a.response is None or b.response is None:
            continue
        size_a = _dataset_size(a.response.parsed_body)
        size_b = _dataset_size(b.response.parsed_body)
        if size_a is None or size_b is None:
            continue
        # Material change: one run empty, the other populated.
        if (size_a == 0) != (size_b == 0) and _request_inputs_identical(a, b):
            warnings.append(
                f"'{a.item_name}': response dataset changed (Run A={size_a}, Run B={size_b}) but the "
                "request inputs were identical across runs - the capture pair may not represent the "
                "same business flow. Not treated as correlation evidence."
            )
    return warnings


def _request_inputs_identical(a: NormalizedExecution, b: NormalizedExecution) -> bool:
    """Compare meaningful request inputs (ignoring runtime-noise headers/cookies)."""
    def sig(e: NormalizedExecution) -> dict[tuple[str, str], str]:
        out: dict[tuple[str, str], str] = {}
        for s in index_request_sinks(e):
            if s.location_type == LocationType.COOKIE:
                continue
            if s.location_type == LocationType.HEADER and (s.key or "").lower() in _NOISE_HEADERS:
                continue
            out[(s.location_type.value, s.canonical_path)] = s.normalized_value
        return out
    return sig(a) == sig(b)


_NOISE_HEADERS = {"postman-token", "content-length", "host", "connection", "accept-encoding", "transfer-encoding", "cookie"}


# --------------------------------------------------------------------------- #
# Readiness gating
# --------------------------------------------------------------------------- #

def compute_readiness(
    baseline: NormalizedRun,
    comparison: NormalizedRun | None,
    alignment: AlignmentReport | None,
    summary: ClassificationSummary,
    scenario_warnings: list[str],
) -> ReadinessReport:
    report = ReadinessReport(scenario_warnings=scenario_warnings)

    # Hard blockers -> NOT_READY.
    blockers = list(baseline.health.blockers)
    if comparison is not None:
        blockers += comparison.health.blockers
    if blockers:
        report.state = ReadinessState.NOT_READY
        report.blockers = blockers
        report.reasons = ["Transport health blocks automatic correlation."]
        return report

    reasons: list[str] = []
    if comparison is None:
        reasons.append("Single run only: values cannot be proven dynamic.")
    elif alignment is not None and alignment.coverage < 1.0:
        reasons.append(f"Only {round(alignment.coverage * 100)}% of requests matched across the two runs.")

    assertion_failures = baseline.health.assertion_failures + (
        comparison.health.assertion_failures if comparison else 0
    )
    if assertion_failures:
        reasons.append(f"{assertion_failures} Newman assertion(s) failed.")

    business = len([s for s in baseline.health.business_signals if s.kind != "assertion_failed"])
    if comparison:
        business += len([s for s in comparison.health.business_signals if s.kind != "assertion_failed"])
    if business:
        reasons.append(f"{business} business-error indicator(s) behind HTTP 2xx responses.")

    if scenario_warnings:
        reasons.append(f"{len(scenario_warnings)} scenario-consistency warning(s).")

    if summary.review_required:
        reasons.append(f"{summary.review_required} value(s) need manual review.")

    report.reasons = reasons
    report.state = ReadinessState.READY if not reasons else ReadinessState.READY_WITH_REVIEW
    return report
