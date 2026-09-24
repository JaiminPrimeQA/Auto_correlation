"""Align executions across two runs by a stable signature (not array index).

Dynamic-looking path segments (UUIDs, long tokens, long numeric ids) are
normalised for the signature only; originals are retained for correlation. A
per-signature occurrence ordinal disambiguates repeated identical requests.
"""

from __future__ import annotations

import re

from ..domain.models import AlignmentPair, AlignmentReport, NormalizedExecution, NormalizedRun

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_LONG_HEX = re.compile(r"^[0-9a-fA-F]{16,}$")
_LONG_NUM = re.compile(r"^\d{5,}$")
_LONG_TOKEN = re.compile(r"^[A-Za-z0-9_-]{20,}$")


def _normalize_segment(seg: str) -> str:
    if _UUID.match(seg) or _LONG_HEX.match(seg) or _LONG_TOKEN.match(seg):
        return "{dyn}"
    if _LONG_NUM.match(seg):
        return "{id}"
    # Prefixed identifiers such as "rec_11112222" or "order-9f8": alphanumeric
    # tokens of reasonable length carrying several digits are treated as dynamic.
    if len(seg) >= 6 and re.fullmatch(r"[A-Za-z0-9_-]+", seg) and sum(c.isdigit() for c in seg) >= 3:
        return "{dyn}"
    return seg


def _path_shape(execution: NormalizedExecution) -> str:
    return "/".join(_normalize_segment(s) for s in execution.request.path_segments)


def signature(execution: NormalizedExecution) -> str:
    r = execution.request
    return f"{r.method}|{r.host.lower()}|{_path_shape(execution)}|{execution.item_name}"


def _indexed_signatures(run: NormalizedRun) -> list[tuple[str, NormalizedExecution]]:
    seen: dict[str, int] = {}
    out: list[tuple[str, NormalizedExecution]] = []
    for e in run.executions:
        base = signature(e)
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.append((f"{base}#{n}", e))
    return out


def align_runs(baseline: NormalizedRun, comparison: NormalizedRun) -> AlignmentReport:
    base_sigs = _indexed_signatures(baseline)
    comp_sigs = _indexed_signatures(comparison)
    comp_map = {sig: e for sig, e in comp_sigs}
    comp_used: set[str] = set()

    pairs: list[AlignmentPair] = []
    matched = missing = extra = reordered = ambiguous = 0

    for order, (sig, be) in enumerate(base_sigs):
        ce = comp_map.get(sig)
        if ce is not None:
            comp_used.add(sig)
            comp_order = next(i for i, (s, _) in enumerate(comp_sigs) if s == sig)
            if comp_order != order:
                reordered += 1
            matched += 1
            pairs.append(
                AlignmentPair(
                    signature=sig,
                    baseline_execution_id=be.id,
                    comparison_execution_id=ce.id,
                    baseline_index=be.original_index,
                    comparison_index=ce.original_index,
                    status="matched",
                )
            )
        else:
            missing += 1
            pairs.append(
                AlignmentPair(
                    signature=sig,
                    baseline_execution_id=be.id,
                    baseline_index=be.original_index,
                    status="missing_in_comparison",
                )
            )

    for sig, ce in comp_sigs:
        if sig not in comp_used:
            extra += 1
            pairs.append(
                AlignmentPair(
                    signature=sig,
                    comparison_execution_id=ce.id,
                    comparison_index=ce.original_index,
                    status="extra_in_comparison",
                )
            )

    total = len(base_sigs)
    coverage = (matched / total) if total else 0.0
    return AlignmentReport(
        pairs=pairs,
        matched=matched,
        missing=missing,
        extra=extra,
        reordered=reordered,
        ambiguous=ambiguous,
        coverage=round(coverage, 4),
    )


def single_run_report(run: NormalizedRun) -> AlignmentReport:
    """Trivial 'alignment' for single-run mode: every execution maps to itself."""
    pairs = [
        AlignmentPair(
            signature=signature(e),
            baseline_execution_id=e.id,
            baseline_index=e.original_index,
            status="matched",
        )
        for e in run.executions
    ]
    return AlignmentReport(pairs=pairs, matched=len(pairs), coverage=1.0 if pairs else 0.0)
