"""Compute run health and gate high-confidence automatic correlation."""

from __future__ import annotations

from collections import Counter

from ..core.config import Settings
from ..domain.enums import HealthLevel
from ..domain.models import AlignmentReport, NormalizedRun, RunHealth
from .business_health import scan_business_signals


def compute_health(run: NormalizedRun, settings: Settings) -> RunHealth:
    execs = run.executions
    total = len(execs)
    dist: Counter[str] = Counter()
    missing = 0
    auth_fail = 0
    server_err = 0
    network_errors = 0
    timeouts = 0
    for e in execs:
        if e.request_error:
            network_errors += 1
            if "timeout" in e.request_error.lower() or "etimedout" in e.request_error.lower():
                timeouts += 1
        if e.response is None or not e.response.present:
            missing += 1
            dist["no_response"] += 1
            continue
        code = e.response.code
        bucket = f"{code // 100}xx" if code else "unknown"
        dist[bucket] += 1
        if code in (401, 403):
            auth_fail += 1
        if 500 <= code < 600:
            server_err += 1

    assertion_failures = sum(1 for e in execs for a in e.assertions if a.failed)
    denom = max(total, 1)
    auth_ratio = auth_fail / denom
    server_ratio = server_err / denom

    health = RunHealth(
        total_executions=total,
        status_distribution=dict(dist),
        missing_responses=missing,
        network_errors=network_errors,
        timeouts=timeouts,
        newman_failures=assertion_failures,
        assertion_failures=assertion_failures,
        auth_failure_ratio=round(auth_ratio, 4),
        server_error_ratio=round(server_ratio, 4),
    )

    # Business/scenario health: HTTP success is not business success.
    signals, empty = scan_business_signals(run)
    health.business_signals = signals
    health.empty_datasets = empty
    if assertion_failures:
        health.warnings.append(
            f"{assertion_failures} Newman assertion(s) failed - HTTP 2xx does not prove the business flow succeeded."
        )
    business_reviews = [s for s in signals if s.kind != "assertion_failed"]
    if business_reviews:
        health.warnings.append(
            f"{len(business_reviews)} business-error indicator(s) found behind HTTP 2xx responses (review required)."
        )
    if network_errors:
        health.warnings.append(f"{network_errors} request(s) had network errors/timeouts.")

    if auth_ratio > settings.auth_failure_block_ratio:
        pct = round(auth_ratio * 100)
        health.blockers.append(
            f"Run is invalid for automatic correlation: {auth_fail} of {total} requests "
            f"returned 401/403 ({pct}%). Capture another successful run with fresh valid authentication."
        )
    if server_ratio > settings.server_error_warn_ratio:
        health.warnings.append(
            f"{server_err} of {total} requests returned 5xx server errors; correlation confidence is reduced."
        )
    if missing:
        health.warnings.append(f"{missing} executions have no captured response.")

    _finalize_level(health)
    return health


def apply_alignment_health(
    baseline: RunHealth,
    comparison: RunHealth,
    alignment: AlignmentReport | None,
    settings: Settings,
) -> None:
    """Add alignment-coverage blockers to the comparison health in-place."""
    if alignment is None:
        return
    if alignment.coverage < settings.min_alignment_coverage:
        pct = round(alignment.coverage * 100)
        comparison.blockers.append(
            f"Sequence alignment coverage is {pct}% (below {round(settings.min_alignment_coverage * 100)}%); "
            "runs are not reliably comparable."
        )
    _finalize_level(comparison)


def _finalize_level(health: RunHealth) -> None:
    if health.blockers:
        health.level = HealthLevel.BLOCKER
    elif health.warnings:
        health.level = HealthLevel.WARNING
    else:
        health.level = HealthLevel.OK
