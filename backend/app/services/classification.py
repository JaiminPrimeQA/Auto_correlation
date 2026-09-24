"""Post-correlation classification.

The correlation engine emits only *proven* producer→consumer candidates. This
module takes those candidates plus the raw runs and assigns every notable value
to exactly one bucket:

    CORRELATION          - proven producer→consumer; receives a JMeter extractor
    COOKIE_MANAGED       - Set-Cookie echoed in later Cookie headers; the HTTP
                           Cookie Manager handles it (no extractor)
    EXTERNAL_CREDENTIAL  - sensitive value with no response producer (env/secret)
    PARAMETERIZATION     - request value that changes across runs with no proven
                           producer (user-supplied input)
    REVIEW_REQUIRED      - suspicious/placeholder value, or ambiguous evidence
    NOISE                - runtime/transport values never reused downstream

Only CORRELATION values become extractors. Nothing unproven is ever counted as
a correlation. Every insight carries an explainable reason.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from ..core.config import Settings
from ..domain.enums import (
    Classification,
    HandlingHint,
    LocationType,
)
from ..domain.models import (
    AlignmentReport,
    ClassificationSummary,
    CorrelationCandidate,
    NormalizedRun,
    ValueInsight,
    ValueOccurrence,
)
from ..utils.masking import is_sensitive_key, looks_like_secret_value, mask_if_sensitive
from .value_indexer import index_request_sinks, index_response_sources

# Transport/runtime headers that must never be replayed and are never business
# correlations. Surfaced once as NOISE, filtered from the generated JMX.
_RUNTIME_NOISE_HEADERS = {
    "postman-token",
    "content-length",
    "host",
    "connection",
    "accept-encoding",
    "transfer-encoding",
}

# Placeholder/suspicious values that must be reviewed rather than replayed.
_SUSPICIOUS_VALUES = {"string", "null", "undefined", "none", "example", "todo", "changeme"}


@dataclass
class ClassificationResult:
    correlations: list[CorrelationCandidate] = field(default_factory=list)
    insights: list[ValueInsight] = field(default_factory=list)
    summary: ClassificationSummary = field(default_factory=ClassificationSummary)


def classify(
    baseline: NormalizedRun,
    comparison: NormalizedRun | None,
    alignment: AlignmentReport | None,
    candidates: list[CorrelationCandidate],
    settings: Settings,
) -> ClassificationResult:
    result = ClassificationResult()

    # --- 1. Split engine candidates: cookie-managed vs true correlation. -----
    for cand in candidates:
        if _is_cookie_managed(cand):
            cand.classification = Classification.COOKIE_MANAGED
            result.insights.append(_cookie_managed_from_candidate(cand))
        else:
            cand.classification = Classification.CORRELATION
            result.correlations.append(cand)

    # --- 2. Request-side discovery (parameterization / credential / noise). --
    producer_values = _producer_values(baseline)
    b2c = _baseline_to_comparison(alignment)
    comp_sinks = _comparison_sink_index(comparison) if comparison else {}

    # A sink that is already a proven correlation consumer (e.g. a token inside
    # an Authorization header) must not be re-classified as a credential.
    correlated_consumers = {
        (con.execution_id, con.location_type, con.canonical_path)
        for cand in result.correlations
        for con in cand.consumers
    }

    # Dedupe insights per logical field (location + key), not per execution.
    field_seen: set[tuple[str, str, str | None]] = set()

    for execution in baseline.executions:
        comp_exec = b2c.get(execution.id)
        for sink in index_request_sinks(execution):
            if (sink.execution_id, sink.location_type, sink.canonical_path) in correlated_consumers:
                continue  # already handled as a correlation consumer
            insight = _classify_request_sink(
                sink=sink,
                comp_sink=comp_sinks.get(
                    (comp_exec, sink.location_type, sink.canonical_path)
                )
                if comp_exec
                else None,
                producer_values=producer_values,
                two_run=comparison is not None,
            )
            if insight is None:
                continue
            field_key = sink.key or sink.canonical_path
            key = (insight.classification.value, sink.location_type.value, field_key)
            if key in field_seen:
                # Attach this occurrence to the existing insight and move on.
                _merge_occurrence(result.insights, key, sink)
                continue
            field_seen.add(key)
            result.insights.append(insight)

    result.summary = _summarize(result)
    return result


# --------------------------------------------------------------------------- #
# Cookie-managed detection
# --------------------------------------------------------------------------- #

def _is_cookie_managed(cand: CorrelationCandidate) -> bool:
    """A candidate is pure Cookie-Manager behaviour when the producer is a
    Set-Cookie value that is only ever consumed as a cookie (COOKIE sink or the
    request ``Cookie`` header). JMeter's HTTP Cookie Manager replays these
    automatically, so no extractor is warranted."""
    if cand.producer.location_type != LocationType.COOKIE:
        return False
    for con in cand.consumers:
        if con.location_type == LocationType.COOKIE:
            continue
        if con.location_type == LocationType.HEADER and (con.key or "").lower() == "cookie":
            continue
        return False  # consumed somewhere a cookie manager cannot handle
    return True


def _cookie_managed_from_candidate(cand: CorrelationCandidate) -> ValueInsight:
    return ValueInsight(
        id=uuid.uuid4().hex,
        classification=Classification.COOKIE_MANAGED,
        handling=HandlingHint.COOKIE_MANAGER,
        variable_name=cand.variable_name,
        location=cand.producer,
        occurrences=list(cand.consumers),
        value_a_masked=cand.evidence.baseline_value_masked,
        value_b_masked=cand.evidence.comparison_value_masked,
        differs_across_runs=cand.evidence.comparison_value_masked is not None,
        reason=(
            f"Cookie '{cand.producer.key or cand.producer.canonical_path}' is set via "
            f"Set-Cookie and replayed in {len(cand.consumers)} later request(s). "
            "The HTTP Cookie Manager handles this automatically."
        ),
        recommended_handling="Handled by HTTP Cookie Manager. Manual extractor = No.",
    )


# --------------------------------------------------------------------------- #
# Request-sink classification
# --------------------------------------------------------------------------- #

def _classify_request_sink(
    *,
    sink: ValueOccurrence,
    comp_sink: ValueOccurrence | None,
    producer_values: set[str],
    two_run: bool,
) -> ValueInsight | None:
    key = (sink.key or "").lower()
    value = sink.raw_value

    # The Cookie header is handled by the Cookie Manager, not classified here.
    if sink.location_type == LocationType.HEADER and key == "cookie":
        return None
    if sink.location_type == LocationType.COOKIE:
        return None

    # A value that a response produced is a correlation concern, not a request
    # value. The engine already decided whether it is a proven correlation.
    if value and value in producer_values:
        return None

    differs = comp_sink is not None and comp_sink.normalized_value != sink.normalized_value
    value_b = comp_sink.raw_value if comp_sink else None

    # Transport/runtime noise headers.
    if sink.location_type == LocationType.HEADER and key in _RUNTIME_NOISE_HEADERS:
        return _insight(
            sink, Classification.NOISE, HandlingHint.IGNORE, value_b, differs,
            reason=f"Runtime/transport header '{sink.key}' is regenerated per request; not a business value.",
            handling_text="Removed from the generated JMX (JMeter recomputes it).",
        )

    # Suspicious / placeholder values need review regardless of source.
    if _is_suspicious(value):
        return _insight(
            sink, Classification.REVIEW_REQUIRED, HandlingHint.MANUAL_REVIEW, value_b, differs,
            reason=f"Value '{value}' looks like a placeholder/example, not real captured data.",
            handling_text="Confirm the real value before running; do not replay a placeholder.",
        )

    # A sensitive KEY (authorization/token/secret/api-key/...) with no producer
    # is an external credential supplied from the environment.
    if is_sensitive_key(sink.key):
        return _insight(
            sink, Classification.EXTERNAL_CREDENTIAL, HandlingHint.JMETER_PROPERTY, value_b, differs,
            reason=(
                f"'{sink.key or sink.canonical_path}' is a sensitive value with no response "
                "producer in the captures - it is supplied externally (environment/secret)."
            ),
            handling_text="Pass at runtime via a JMeter property, e.g. ${__P(NAME,)} with -JNAME=...",
        )

    # A secret-SHAPED value (GUID/JWT/Bearer) under a non-sensitive key is
    # ambiguous - it may be an id or a secret. Never auto-externalize or
    # fabricate a correlation; ask for review.
    if looks_like_secret_value(value):
        return _insight(
            sink, Classification.REVIEW_REQUIRED, HandlingHint.MANUAL_REVIEW, value_b, differs,
            reason=(
                f"'{sink.key or sink.canonical_path}' is a token/GUID-shaped value with no "
                "response producer in the captures. It could be an id or a secret - review before replay."
            ),
            handling_text="Confirm whether this is a static id (leave as-is), a secret (JMeter property), or dynamic (needs a producer capture).",
        )

    # Request value that changes across runs with no producer → parameterization.
    if two_run and differs and value:
        return _insight(
            sink, Classification.PARAMETERIZATION, HandlingHint.USER_DEFINED_VARIABLE, value_b, differs,
            reason=(
                f"'{sink.key or sink.canonical_path}' changes across runs "
                "but no earlier response produces it - user-supplied input, not a correlation."
            ),
            handling_text="Parameterize via User Defined Variables or a CSV Data Set (not a correlation).",
        )

    return None


def _is_suspicious(value: str) -> bool:
    v = (value or "").strip().lower()
    return v in _SUSPICIOUS_VALUES


def _insight(
    sink: ValueOccurrence,
    classification: Classification,
    handling: HandlingHint,
    value_b: str | None,
    differs: bool,
    *,
    reason: str,
    handling_text: str,
) -> ValueInsight:
    return ValueInsight(
        id=uuid.uuid4().hex,
        classification=classification,
        handling=handling,
        variable_name=None,
        location=sink,
        occurrences=[sink],
        value_a_masked=mask_if_sensitive(sink.key, sink.raw_value),
        value_b_masked=mask_if_sensitive(sink.key, value_b) if value_b is not None else None,
        differs_across_runs=differs,
        reason=reason,
        recommended_handling=handling_text,
    )


def _merge_occurrence(
    insights: list[ValueInsight], key: tuple[str, str, str | None], sink: ValueOccurrence
) -> None:
    for ins in insights:
        field_key = ins.location.key or ins.location.canonical_path
        if (ins.classification.value, ins.location.location_type.value, field_key) == key:
            ins.occurrences.append(sink)
            return


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _producer_values(run: NormalizedRun) -> set[str]:
    values: set[str] = set()
    for e in run.executions:
        if not e.has_usable_response:
            continue
        for src in index_response_sources(e):
            if src.raw_value:
                values.add(src.raw_value)
    return values


def _baseline_to_comparison(alignment: AlignmentReport | None) -> dict[str, str]:
    if alignment is None:
        return {}
    return {
        p.baseline_execution_id: p.comparison_execution_id
        for p in alignment.pairs
        if p.status == "matched" and p.baseline_execution_id and p.comparison_execution_id
    }


def _comparison_sink_index(
    run: NormalizedRun,
) -> dict[tuple[str, LocationType, str], ValueOccurrence]:
    idx: dict[tuple[str, LocationType, str], ValueOccurrence] = {}
    for e in run.executions:
        for sink in index_request_sinks(e):
            idx[(e.id, sink.location_type, sink.canonical_path)] = sink
    return idx


def _summarize(result: ClassificationResult) -> ClassificationSummary:
    summary = ClassificationSummary(correlations=len(result.correlations))
    for ins in result.insights:
        if ins.classification == Classification.PARAMETERIZATION:
            summary.parameterizations += 1
        elif ins.classification == Classification.EXTERNAL_CREDENTIAL:
            summary.external_credentials += 1
        elif ins.classification == Classification.COOKIE_MANAGED:
            summary.cookie_managed += 1
        elif ins.classification == Classification.NOISE:
            summary.noise += 1
        elif ins.classification == Classification.REVIEW_REQUIRED:
            summary.review_required += 1
    return summary
