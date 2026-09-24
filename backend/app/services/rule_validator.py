"""Validate manual / edited correlation rules with specific, coded errors."""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.enums import ExtractorMethod, LocationType, Side
from ..domain.models import CorrelationRule, NormalizedExecution, NormalizedRun
from ..utils.naming import is_valid_variable_name
from .jsonpath_support import jsonpath_values, scalar_text
from .value_indexer import (
    index_request_sinks,
    index_response_sources,
)


@dataclass
class ValidationIssue:
    code: str
    detail: str


def resolve_extractor(execution: NormalizedExecution, method: ExtractorMethod, expr: str, match_number: int = 1) -> str | None:
    """Return the value an extractor would capture from the producer response."""
    resp = execution.response
    if resp is None or not resp.present:
        return None
    if method == ExtractorMethod.JSON_PATH:
        if resp.parsed_body is None:
            return None
        values = jsonpath_values(resp.parsed_body, expr)
        return scalar_text(values[match_number - 1]) if 0 < match_number <= len(values) else None
    if method == ExtractorMethod.XPATH2:
        for src in index_response_sources(execution):
            if src.location_type == LocationType.XML_BODY and src.canonical_path == expr:
                return src.raw_value
        return None
    if method in (ExtractorMethod.REGEX, ExtractorMethod.BOUNDARY):
        import re

        headers_text = "\n".join(f"{h.name}: {h.value}" for h in resp.headers)
        for haystack in (headers_text, resp.raw_body_text):
            try:
                m = re.search(expr, haystack)
            except re.error:
                return None
            if m:
                return m.group(1) if m.groups() else m.group(0)
        # boundary path may reference a header/cookie name directly
        for src in index_response_sources(execution):
            if src.canonical_path == expr:
                return src.raw_value
        return None
    return None


def _ambiguous_detail(matching, producer_exec, value, selected_path, comparison_run):
    """Explain WHY an ambiguous source is unsafe and what to do instead."""
    import re

    locations = {(s.location_type, s.canonical_path) for s in matching}
    count = len(locations)
    is_array = any(re.search(r"\[\d+\]", path) for _, path in locations)

    if is_array:
        idx_match = re.search(r"\[\d+\]", selected_path or "")
        idx = idx_match.group(0) if idx_match else "[N]"
        head = (
            f"This value appears in {count} rows of a repeating array. "
            f"Row order and size change between runs, so a fixed index like {idx} is unreliable."
        )
    else:
        head = f"This value appears in {count} different response locations, so a single source is ambiguous."

    absent = _absent_in_comparison(producer_exec, value, comparison_run)
    tail = (
        " It is also absent/empty in the comparison run, so the extractor would capture nothing there."
        if absent else ""
    )
    advice = (
        " If this is an input you already provide (e.g. an id you query by), it is a parameter, not a "
        "correlation - use a User Defined Variable or CSV Data Set instead. Otherwise pick a source "
        "where the value appears exactly once."
    )
    return head + tail + advice


def _absent_in_comparison(producer_exec, value, comparison_run) -> bool:
    """True when the aligned comparison-run response does not contain the value
    at all (e.g. the producer array was empty in the other run)."""
    if comparison_run is None or producer_exec is None:
        return False
    for e in comparison_run.executions:
        if e.item_name == producer_exec.item_name and e.method == producer_exec.method:
            values = {s.raw_value for s in index_response_sources(e)}
            return value not in values
    return False


def validate_rule(
    rule: CorrelationRule,
    producer_run: NormalizedRun,
    consumer_run: NormalizedRun,
    existing_names: set[str],
    comparison_run: NormalizedRun | None = None,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    by_id_producer = {e.id: e for e in producer_run.executions}
    by_id_consumer = {e.id: e for e in consumer_run.executions}

    # Name validity / uniqueness
    if not is_valid_variable_name(rule.variable_name):
        issues.append(ValidationIssue("invalid_variable_name", "Variable name must match [A-Za-z_][A-Za-z0-9_]*."))
    if rule.variable_name in existing_names:
        issues.append(ValidationIssue(
            "duplicate_variable_name",
            f"Variable '{rule.variable_name}' is already used by another rule. You can create as many "
            f"rules as you like, but each needs a unique name - rename this one (e.g. '{rule.variable_name}_2').",
        ))

    producer_exec = by_id_producer.get(rule.producer.execution_id)
    if producer_exec is None:
        issues.append(ValidationIssue("producer_not_found", "Producer execution not found."))
        return issues

    # Producer side must be a response (extractor source).
    if rule.producer.side != Side.RESPONSE:
        issues.append(ValidationIssue(
            "no_extractor_source",
            "The selected value is request data, so there is no response to extract it from.",
        ))

    # Source value must exist in the producer response.
    sources = index_response_sources(producer_exec)
    matching = [s for s in sources if s.raw_value == rule.producer.raw_value]
    if not matching:
        issues.append(ValidationIssue(
            "value_not_in_response",
            "The selected value was not found in any response.",
        ))
    elif len({(s.location_type, s.canonical_path) for s in matching}) > 1:
        issues.append(ValidationIssue(
            "ambiguous_producers",
            _ambiguous_detail(
                matching, producer_exec, rule.producer.raw_value,
                rule.producer.canonical_path, comparison_run,
            ),
        ))

    # Producer response must be usable.
    if producer_exec.response is not None and producer_exec.response.present:
        code = producer_exec.response.code
        if code and not (200 <= code < 400):
            issues.append(ValidationIssue(
                "source_response_failed",
                f"Source response failed or was unauthorized (HTTP {code}).",
            ))

    # Extractor must resolve to the expected value.
    if rule.match_number < 1:
        issues.append(ValidationIssue(
            "unsupported_match_number", "A scalar correlation requires a positive match number; random/all matches cannot be wired to one deterministic variable."
        ))
    resolved = resolve_extractor(producer_exec, rule.extractor_method, rule.extractor_expression, rule.match_number)
    if resolved is None:
        issues.append(ValidationIssue(
            "extractor_unresolved",
            "The extractor expression does not resolve against the captured response.",
        ))
    elif matching and resolved != rule.producer.raw_value:
        issues.append(ValidationIssue(
            "extractor_mismatch",
            "The extractor resolves to a different value than the selected source.",
        ))

    # Consumers must be downstream and actually contain the value.
    if not rule.consumers:
        issues.append(ValidationIssue("no_consumers", "No downstream request contains the selected value."))
    for con in rule.consumers:
        con_exec = by_id_consumer.get(con.execution_id)
        if con_exec is None:
            issues.append(ValidationIssue("consumer_not_found", f"Consumer execution {con.execution_id} not found."))
            continue
        if not rule.expert_override and con_exec.original_index <= producer_exec.original_index:
            issues.append(ValidationIssue(
                "consumer_not_downstream",
                f"Consumer '{con_exec.item_name}' is not later than the producer.",
            ))
        sinks = index_request_sinks(con_exec)
        found = any(
            s.location_type == con.location_type and s.canonical_path == con.canonical_path
            for s in sinks
        )
        if not found:
            issues.append(ValidationIssue(
                "consumer_location_missing",
                f"Consumer location {con.location_type.value}:{con.canonical_path} not present.",
            ))

    return issues
