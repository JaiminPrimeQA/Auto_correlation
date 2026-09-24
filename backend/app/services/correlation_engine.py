"""Deterministic automatic-correlation engine.

A two-run candidate is high confidence only when ALL of these hold:
  1. A scalar value exists in an earlier producer response in Run A.
  2. The same source location exists in the aligned producer response in Run B.
  3. The Run A and Run B source values differ (proves the value is dynamic).
  4. The Run A source value appears in a later Run A request.
  5. The Run B source value appears in the aligned later Run B request at the
     equivalent semantic location.
  6. The producer occurs before every proposed consumer.
  7. The producer response is usable (not an auth/server error).
  8. The proposed replacement is token-aware (whole value / known wrapper).

No LLM is used. Scoring is explainable; no unexplained percentages.
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict

from ..core.config import Settings
from ..domain.enums import (
    CandidateState,
    Confidence,
    DataType,
    ExtractorMethod,
    LocationType,
)
from ..domain.models import (
    AlignmentReport,
    CorrelationCandidate,
    Evidence,
    NormalizedExecution,
    NormalizedRun,
    ValueOccurrence,
)
from ..utils.masking import mask_value
from ..utils.naming import dedupe_variable_name, suggest_variable_name
from .value_indexer import index_request_sinks, index_response_sources

# Values that are never dynamic business correlations.
_COMMON_CONSTANTS = {
    "get", "post", "put", "delete", "patch", "head", "options",
    "true", "false", "null", "none",
    "application/json", "application/xml", "text/html", "text/plain",
    "application/x-www-form-urlencoded", "multipart/form-data",
    "ok", "created", "accepted", "no content", "bad request", "unauthorized",
    "forbidden", "not found", "internal server error", "utf-8", "gzip",
    "keep-alive", "close", "no-cache", "*/*",
}


def _shannon_entropy_per_char(value: str) -> float:
    if not value:
        return 0.0
    counts: dict[str, int] = defaultdict(int)
    for ch in value:
        counts[ch] += 1
    n = len(value)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _is_excluded(occ: ValueOccurrence, settings: Settings) -> str | None:
    """Return an exclusion reason, or None if the value may be correlated."""
    if occ.data_type in (DataType.NULL, DataType.BOOLEAN):
        return "value is null/boolean"
    v = occ.normalized_value
    if not v:
        return "empty value"
    if len(v) < settings.min_dynamic_value_length:
        return "value too short/common"
    if v.lower() in _COMMON_CONSTANTS:
        return "common HTTP constant"
    if occ.data_type == DataType.NUMBER and len(v) <= 3:
        return "small enum-like integer"
    return None


def _extractor_for(occ: ValueOccurrence) -> tuple[ExtractorMethod, str]:
    if occ.location_type == LocationType.JSON_BODY:
        return ExtractorMethod.JSON_PATH, occ.canonical_path
    if occ.location_type == LocationType.XML_BODY:
        return ExtractorMethod.XPATH2, occ.canonical_path
    if occ.location_type in (LocationType.HEADER, LocationType.COOKIE):
        # Header/cookie value -> regex on the response headers.
        return ExtractorMethod.REGEX, _header_regex(occ)
    if occ.location_type == LocationType.TEXT_BODY:
        # canonical_path already carries a body regex with one capture group.
        return ExtractorMethod.REGEX, occ.canonical_path
    return ExtractorMethod.BOUNDARY, occ.canonical_path


def _header_regex(occ: ValueOccurrence) -> str:
    # Applied against the response headers field in JMeter.
    return rf"{_escape_regex(occ.key or occ.canonical_path)}:\s*(.+?)[\r\n]"


def _escape_regex(text: str) -> str:
    import re

    return re.escape(text)


def _match_consumer(value: str, sink: ValueOccurrence) -> tuple[bool, str | None]:
    """Token-aware match of a producer value against a consumer sink.

    Returns (matched, wrapper). Structured leaves and path/query/form require a
    whole-value match; headers additionally accept a ``Bearer <value>`` wrapper.
    Text bodies allow a substring match (lower confidence, handled by scoring).
    """
    sv = sink.raw_value
    if sv == value:
        return True, None
    if sink.location_type == LocationType.HEADER:
        for prefix in ("Bearer ", "bearer ", "Token ", "token "):
            if sv == prefix + value:
                return True, prefix
    if sink.location_type == LocationType.TEXT_BODY and len(value) >= 8 and value in sv:
        return True, None
    return False, None


class CorrelationEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def analyze_two_run(
        self,
        baseline: NormalizedRun,
        comparison: NormalizedRun,
        alignment: AlignmentReport,
    ) -> list[CorrelationCandidate]:
        b2c = {
            p.baseline_execution_id: p.comparison_execution_id
            for p in alignment.pairs
            if p.status == "matched" and p.baseline_execution_id and p.comparison_execution_id
        }

        # Pre-index comparison sinks/sources by (exec_id, loc, path).
        comp_sink_idx = self._index_by_location(comparison, sinks=True)
        comp_source_idx = self._index_by_location(comparison, sinks=False)

        # baseline sinks grouped by execution index for downstream scanning.
        base_sinks: list[ValueOccurrence] = [
            s for e in baseline.executions for s in index_request_sinks(e)
        ]

        raw_candidates: list[CorrelationCandidate] = []
        taken_names: set[str] = set()

        for exec_a in baseline.executions:
            if not exec_a.has_usable_response:
                continue
            producer_usable = _response_usable(exec_a)
            comp_exec_id = b2c.get(exec_a.id)
            for src_a in index_response_sources(exec_a):
                if _is_excluded(src_a, self.settings):
                    continue
                # Condition 2: same location exists in aligned comparison producer.
                src_b = comp_source_idx.get((comp_exec_id, src_a.location_type, src_a.canonical_path)) if comp_exec_id else None
                if src_b is None:
                    continue
                # Condition 3: values differ (dynamic).
                if src_a.normalized_value == src_b.normalized_value:
                    continue

                value_a = src_a.raw_value
                value_b = src_b.raw_value

                consumers: list[ValueOccurrence] = []
                schema_changed = False
                text_extraction = False
                for sink_a in base_sinks:
                    if sink_a.execution_index <= exec_a.original_index:
                        continue  # Condition 6: producer must precede consumer.
                    matched_a, wrapper = _match_consumer(value_a, sink_a)
                    if not matched_a:
                        continue
                    # Condition 5: aligned comparison sink holds value_b.
                    comp_sink_exec = b2c.get(_exec_id_of(sink_a))
                    if comp_sink_exec is None:
                        schema_changed = True
                        continue  # consumer request not aligned across runs
                    sink_b = comp_sink_idx.get(
                        (comp_sink_exec, sink_a.location_type, sink_a.canonical_path)
                    )
                    if sink_b is None:
                        schema_changed = True
                        continue
                    matched_b, _ = _match_consumer(value_b, sink_b)
                    if not matched_b:
                        schema_changed = True
                        continue
                    if sink_a.location_type == LocationType.TEXT_BODY:
                        text_extraction = True
                    consumer = sink_a.model_copy(update={"wrapper": wrapper})
                    consumers.append(consumer)

                if not consumers:
                    continue

                method, expr = _extractor_for(src_a)
                if method in (ExtractorMethod.REGEX, ExtractorMethod.BOUNDARY):
                    text_extraction = True
                var = dedupe_variable_name(
                    suggest_variable_name(src_a.key, src_a.canonical_path), taken_names
                )
                evidence = self._score(
                    two_run=True,
                    value=value_a,
                    structured=method in (ExtractorMethod.JSON_PATH, ExtractorMethod.XPATH2, ExtractorMethod.JSON_JMESPATH),
                    consumer_count=len(consumers),
                    text_extraction=text_extraction,
                    schema_changed=schema_changed,
                    producer_usable=producer_usable,
                    value_b=value_b,
                )
                candidate = CorrelationCandidate(
                    id=uuid.uuid4().hex,
                    variable_name=var,
                    producer=src_a,
                    consumers=consumers,
                    extractor_method=method,
                    extractor_expression=expr,
                    confidence=_classify(evidence.score),
                    evidence=evidence,
                    warnings=[] if producer_usable else ["Producer response is a non-success/error response."],
                    state=CandidateState.SUGGESTED,
                )
                raw_candidates.append(candidate)

        return self._dedupe_and_flag_conflicts(raw_candidates)

    def analyze_single_run(self, run: NormalizedRun) -> list[CorrelationCandidate]:
        """Lower-confidence producer→consumer discovery within one run."""
        sinks = [s for e in run.executions for s in index_request_sinks(e)]
        raw: list[CorrelationCandidate] = []
        taken: set[str] = set()
        for exec_a in run.executions:
            if not exec_a.has_usable_response:
                continue
            producer_usable = _response_usable(exec_a)
            for src in index_response_sources(exec_a):
                if _is_excluded(src, self.settings):
                    continue
                consumers: list[ValueOccurrence] = []
                text_extraction = False
                for sink in sinks:
                    if sink.execution_index <= exec_a.original_index:
                        continue
                    matched, wrapper = _match_consumer(src.raw_value, sink)
                    if matched:
                        if sink.location_type == LocationType.TEXT_BODY:
                            text_extraction = True
                        consumers.append(sink.model_copy(update={"wrapper": wrapper}))
                if not consumers:
                    continue
                method, expr = _extractor_for(src)
                if method in (ExtractorMethod.REGEX, ExtractorMethod.BOUNDARY):
                    text_extraction = True
                var = dedupe_variable_name(suggest_variable_name(src.key, src.canonical_path), taken)
                evidence = self._score(
                    two_run=False,
                    value=src.raw_value,
                    structured=method in (ExtractorMethod.JSON_PATH, ExtractorMethod.XPATH2),
                    consumer_count=len(consumers),
                    text_extraction=text_extraction,
                    schema_changed=False,
                    producer_usable=producer_usable,
                    value_b=None,
                )
                raw.append(
                    CorrelationCandidate(
                        id=uuid.uuid4().hex,
                        variable_name=var,
                        producer=src,
                        consumers=consumers,
                        extractor_method=method,
                        extractor_expression=expr,
                        confidence=_classify(evidence.score),
                        evidence=evidence,
                        warnings=["Single-run mode: cannot prove the value is dynamic."],
                        state=CandidateState.SUGGESTED,
                    )
                )
        return self._dedupe_and_flag_conflicts(raw)

    # --- internals ---

    def _index_by_location(
        self, run: NormalizedRun, *, sinks: bool
    ) -> dict[tuple[str, LocationType, str], ValueOccurrence]:
        idx: dict[tuple[str, LocationType, str], ValueOccurrence] = {}
        for e in run.executions:
            occs = index_request_sinks(e) if sinks else index_response_sources(e)
            for o in occs:
                idx[(e.id, o.location_type, o.canonical_path)] = o
        return idx

    def _score(
        self,
        *,
        two_run: bool,
        value: str,
        structured: bool,
        consumer_count: int,
        text_extraction: bool,
        schema_changed: bool,
        producer_usable: bool,
        value_b: str | None,
    ) -> Evidence:
        factors: list[str] = []
        penalties: list[str] = []
        score = 0.0

        if two_run:
            score += 40
            factors.append("Producer→consumer match proven in both runs.")
            score += 10
            factors.append("Source path stable across both runs.")
            score += 15
            factors.append("Consumer location identical in both runs.")
        else:
            score += 15
            penalties.append("Only one run available (dynamism unproven).")
            score -= 30

        if structured:
            score += 10
            factors.append("Structured (JSON/XML) extractor available.")

        entropy = _shannon_entropy_per_char(value)
        if len(value) >= 12 and entropy >= self.settings.token_entropy_hint:
            score += 10
            factors.append(f"Token-like value (len={len(value)}, entropy={entropy:.2f}).")
        elif len(value) < 6:
            score -= 20
            penalties.append("Value is short/common.")

        if consumer_count > 1:
            bonus = min(15, 5 * (consumer_count - 1))
            score += bonus
            factors.append(f"Reused by {consumer_count} downstream requests.")

        if text_extraction:
            score -= 15
            penalties.append("Regex/text extraction required.")
        if schema_changed:
            score -= 15
            penalties.append("Some consumer locations changed between runs.")
        if not producer_usable:
            score -= 25
            penalties.append("Source response is a non-success/error response.")

        return Evidence(
            factors=factors,
            penalties=penalties,
            score=round(max(score, 0.0), 1),
            baseline_value_masked=mask_value(value),
            comparison_value_masked=mask_value(value_b) if value_b is not None else None,
        )

    def _dedupe_and_flag_conflicts(
        self, candidates: list[CorrelationCandidate]
    ) -> list[CorrelationCandidate]:
        # Merge duplicates sharing a producer occurrence + variable.
        merged: dict[tuple[str, str, str], CorrelationCandidate] = {}
        for c in candidates:
            key = (c.producer.execution_id, c.producer.canonical_path, c.variable_name)
            if key in merged:
                existing = merged[key]
                seen = {(x.execution_id, x.location_type, x.canonical_path) for x in existing.consumers}
                for con in c.consumers:
                    if (con.execution_id, con.location_type, con.canonical_path) not in seen:
                        existing.consumers.append(con)
            else:
                merged[key] = c

        result = list(merged.values())

        # A downstream API often echoes an identifier that was already supplied
        # in its request. Do not present that echo as a second producer when the
        # original producer already covers all of the echo's later consumers.
        # Keeping the earliest source avoids duplicate extractors such as
        # ``id`` and ``id_2`` for the same logical value.
        def consumer_keys(candidate: CorrelationCandidate) -> set[tuple[str, LocationType, str]]:
            return {
                (consumer.execution_id, consumer.location_type, consumer.canonical_path)
                for consumer in candidate.consumers
            }

        redundant: set[str] = set()
        for later in result:
            later_consumers = consumer_keys(later)
            for earlier in result:
                if earlier.id == later.id:
                    continue
                if earlier.producer.execution_index >= later.producer.execution_index:
                    continue
                if earlier.producer.raw_value != later.producer.raw_value:
                    continue
                if not any(
                    consumer.execution_id == later.producer.execution_id
                    for consumer in earlier.consumers
                ):
                    continue
                if later_consumers <= consumer_keys(earlier):
                    redundant.add(later.id)
                    break
        result = [candidate for candidate in result if candidate.id not in redundant]

        # Flag conflicts: distinct producers feeding the same consumer location.
        by_consumer: dict[tuple[str, LocationType, str], list[CorrelationCandidate]] = defaultdict(list)
        for c in result:
            for con in c.consumers:
                by_consumer[(con.execution_id, con.location_type, con.canonical_path)].append(c)
        for cands in by_consumer.values():
            producers = {(c.producer.execution_id, c.producer.canonical_path) for c in cands}
            if len(producers) > 1:
                for c in cands:
                    msg = "Conflict: multiple producers could supply a shared consumer; review required."
                    if msg not in c.warnings:
                        c.warnings.append(msg)

        result.sort(key=lambda c: c.evidence.score, reverse=True)
        return result


def _response_usable(execution: NormalizedExecution) -> bool:
    resp = execution.response
    if resp is None or not resp.present:
        return False
    code = resp.code
    return code == 0 or (200 <= code < 400)


def _exec_id_of(sink: ValueOccurrence) -> str:
    return sink.execution_id


def _classify(score: float) -> Confidence:
    if score >= 70:
        return Confidence.HIGH
    if score >= 45:
        return Confidence.MEDIUM
    if score > 0:
        return Confidence.LOW
    return Confidence.REJECTED
