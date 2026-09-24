"""Build one rule per response source, collecting all proven downstream uses.

Exact reuse can identify any row in an array. Placeholder matching uses only
responses and request selections already seen at that point in the journey;
neither the first array row nor a later request is evidence of a selection.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter, defaultdict

from ..core.config import Settings
from ..domain.enums import Confidence, DataType, LocationType, RuleOrigin, RuleState
from ..domain.models import CorrelationRule, NormalizedRun, ValueOccurrence
from ..utils.naming import (
    dedupe_variable_name,
    fields_are_equivalent,
    is_placeholder_value,
    normalize_field_name,
    suggest_variable_name,
)
from .consumer_finder import _field_name, is_correlatable_source
from .correlation_engine import _extractor_for, _is_excluded, _match_consumer, _response_usable
from .value_indexer import index_request_sinks, index_response_sources

_ARRAY_ROW = re.compile(r"^(.*)\[(\d+)\](?:\.[A-Za-z_]\w*|\['[^']*'\])$")


def _row(src: ValueOccurrence) -> tuple[str, str] | None:
    match = _ARRAY_ROW.match(src.canonical_path)
    if src.location_type == LocationType.JSON_BODY and match:
        return match.group(1), f"{match.group(1)}[{match.group(2)}]"
    return None


def auto_correlate(run: NormalizedRun, settings: Settings) -> list[CorrelationRule]:
    sources: list[ValueOccurrence] = []
    rules: dict[tuple[str, LocationType, str], CorrelationRule] = {}
    taken_names: set[str] = set()
    selected_rows: dict[tuple[str, str], set[str]] = defaultdict(set)

    def add(src: ValueOccurrence, sink: ValueOccurrence, wrapper: str | None, inferred: bool) -> None:
        key = (src.execution_id, src.location_type, src.canonical_path)
        if key not in rules:
            method, expr = _extractor_for(src)
            rules[key] = CorrelationRule(
                id=uuid.uuid4().hex,
                variable_name=dedupe_variable_name(suggest_variable_name(src.key, src.canonical_path), taken_names),
                origin=RuleOrigin.AUTOMATIC, state=RuleState.ENABLED,
                producer=src, extractor_method=method, extractor_expression=expr,
                confidence=Confidence.MEDIUM,
            )
        rule = rules[key]
        rule.consumers.append(sink.model_copy(update={"wrapper": wrapper}))
        if inferred and not rule.warnings:
            rule.warnings.append("Includes a placeholder matched by field name to a uniquely selected prior source. Review before load.")

    for execution in sorted(run.executions, key=lambda e: e.original_index):
        pending: list[ValueOccurrence] = []
        # Resolve concrete values first, including short identifiers under an
        # equivalent name. Those selections may disambiguate another field in
        # the SAME request, but can never affect an earlier request.
        for sink in index_request_sinks(execution):
            candidates = []
            for src in sources:
                matched, wrapper = _match_consumer(src.raw_value, sink)
                named_identifier = (
                    len(normalize_field_name(_field_name(src))) >= 4
                    and fields_are_equivalent(_field_name(src), _field_name(sink))
                    and normalize_field_name(_field_name(src)).endswith(("id", "guid", "uuid"))
                )
                if matched and (not _is_excluded(src, settings) or named_identifier):
                    candidates.append((src, wrapper))
            if candidates:
                src, wrapper = candidates[0]
                add(src, sink, wrapper, False)
                row = _row(src)
                if row:
                    selected_rows[(src.execution_id, row[0])].add(row[1])
            elif is_placeholder_value(sink.raw_value):
                pending.append(sink)

        for sink in pending:
            name = _field_name(sink)
            if len(normalize_field_name(name)) < 4:
                continue
            matches = [src for src in sources if fields_are_equivalent(_field_name(src), name)]
            by_array: dict[tuple[str, str], list[ValueOccurrence]] = defaultdict(list)
            candidates = []
            for src in matches:
                row = _row(src)
                if row:
                    by_array[(src.execution_id, row[0])].append(src)
                else:
                    candidates.append(src)
            ambiguous = False
            for group, members in by_array.items():
                if len(members) > 1:
                    chosen = selected_rows[group]
                    members = [src for src in members if _row(src)[1] in chosen] if len(chosen) == 1 else []
                if len(members) != 1:
                    ambiguous = True
                    break
                candidates.extend(members)
            if not ambiguous and len({src.raw_value for src in candidates}) == 1:
                src = min(candidates, key=lambda source: source.execution_index)
                add(src, sink, None, True)

        # The current response only becomes available AFTER its request.
        if _response_usable(execution):
            produced = index_response_sources(execution)
            counts = Counter(src.raw_value for src in produced)
            sources.extend(
                src for src in produced
                if src.location_type != LocationType.COOKIE
                and is_correlatable_source(src)
                and src.data_type not in (DataType.NULL, DataType.BOOLEAN)
                and counts[src.raw_value] == 1
                and src.raw_value
                and not is_placeholder_value(src.raw_value)
            )
    return list(rules.values())
