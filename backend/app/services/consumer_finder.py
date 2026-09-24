"""Given a value produced by a response, find every later request that uses it.

This powers the assisted ("pick a response value → auto-wire it") flow. It uses
the same token-aware matching as the automatic engine (whole-value match, plus
known wrappers like ``Bearer <value>`` and URL-encoding), and only ever looks at
requests that occur AFTER the producer. Works for any response source: JSON
body, XML, response headers, or Set-Cookie values.
"""

from __future__ import annotations

import re

from ..domain.enums import ExtractorMethod, LocationType
from ..domain.models import NormalizedRun, ValueOccurrence
from ..utils.naming import fields_are_equivalent, is_placeholder_value, normalize_field_name
from .correlation_engine import _extractor_for, _match_consumer
from .value_indexer import index_request_sinks, index_response_sources

# Detects an array index greater than zero, e.g. "$[3].merchantGUID". The
# first-object policy (spec §8) means only $[0]/top-level fields are treated as
# the canonical producer for name-alias substitution.
_NON_FIRST_INDEX = re.compile(r"\[(?!0\])\d+\]")


def response_sources(execution) -> list[ValueOccurrence]:
    return index_response_sources(execution)


def find_producer_source(
    run: NormalizedRun, execution_id: str, location_type: str, canonical_path: str
) -> tuple[object, ValueOccurrence] | None:
    """Locate the producer execution and the selected response value."""
    for e in run.executions:
        if e.id == execution_id:
            for src in index_response_sources(e):
                if src.location_type.value == location_type and src.canonical_path == canonical_path:
                    return e, src
            return e, None  # execution found, value not
    return None


def find_consumers(
    run: NormalizedRun, producer_exec, producer_src: ValueOccurrence
) -> list[tuple[object, ValueOccurrence, str | None]]:
    """Return (consumer_execution, sink, wrapper) for every later request that
    contains the producer value."""
    out: list[tuple[object, ValueOccurrence, str | None]] = []
    value = producer_src.raw_value
    if not value:
        return out
    for e in run.executions:
        if e.original_index <= producer_exec.original_index:
            continue  # producer must precede consumer
        for sink in index_request_sinks(e):
            matched, wrapper = _match_consumer(value, sink)
            if matched:
                out.append((e, sink, wrapper))
    return out


def is_first_object_source(producer_src: ValueOccurrence) -> bool:
    """True for a top-level field or the first array element ($[0].x)."""
    return _NON_FIRST_INDEX.search(producer_src.canonical_path) is None


def find_alias_consumers(
    run: NormalizedRun, producer_exec, producer_src: ValueOccurrence,
    allow_array_row: bool = False,
) -> list[tuple[object, ValueOccurrence, str | None]]:
    """Find later requests that reuse the producer field by NAME (not exact value).

    This is what lets the engine correlate consumers the exact-value matcher
    misses:
      - a placeholder value (``merchantsGuid=string``) whose field name matches
        the producer through an approved alias, and
      - a short/enum-like value (``MerchantID=6``) that value-threading excludes
        as noise but whose field name uniquely matches an earlier producer.

    Only the first-object/top-level producer is used (spec §8), and only field
    names of real length (>= 4 normalised) participate, so generic ``id`` never
    triggers a blind match. Callers must ensure the producer is the unique
    earlier producer for this field name before trusting a placeholder match.
    """
    out: list[tuple[object, ValueOccurrence, str | None]] = []
    p_name = _field_name(producer_src)
    p_norm = normalize_field_name(p_name)
    if len(p_norm) < 4 or (not is_first_object_source(producer_src) and not allow_array_row):
        return out
    value = producer_src.raw_value
    for e in run.executions:
        if e.original_index <= producer_exec.original_index:
            continue  # producer must precede consumer
        for sink in index_request_sinks(e):
            if not fields_are_equivalent(p_name, _field_name(sink)):
                continue
            sv = sink.raw_value
            if sv == value:
                out.append((e, sink, None))  # exact value under a matching name
            elif is_placeholder_value(sv):
                out.append((e, sink, None))  # placeholder standing in for the value
    return out


def find_name_matches(
    run: NormalizedRun, producer_exec, producer_src: ValueOccurrence
) -> list[tuple[object, ValueOccurrence, str, str]]:
    """Suggest correlations by matching FIELD NAMES (not values).

    Catches the case where a producer field and a later request field share a
    name but hold different captured values (e.g. the report returns the real
    ``merchantGUID`` while the update request has a hardcoded ``merchantsGuid``).
    This is an inference, not proof, so callers must present it as review-only.
    Names are normalised (case/separators removed) and matched within one edit,
    so ``webhookId``≈``webhookID``≈``webhook_id`` and ``merchantGUID``≈``merchantsGuid``.
    Generic short names (< 4 chars, e.g. ``id``) are skipped to avoid noise.
    """
    p_name = _field_name(producer_src)
    p_norm = _norm(p_name)
    if len(p_norm) < 4:
        return []
    value = producer_src.raw_value
    out: list[tuple[object, ValueOccurrence, str, str]] = []
    for e in run.executions:
        if e.original_index <= producer_exec.original_index:
            continue
        for sink in index_request_sinks(e):
            if sink.raw_value == value:
                continue  # exact-value match is handled by find_consumers
            c_name = _field_name(sink)
            c_norm = _norm(c_name)
            if len(c_norm) < 4:
                continue
            if fields_are_equivalent(p_name, c_name):
                out.append((e, sink, p_name, c_name))
    return out


def _field_name(occ: ValueOccurrence) -> str:
    if occ.key:
        return occ.key
    import re

    segs = [s for s in re.split(r"[.\[\]/]", occ.canonical_path) if s and s != "$" and not s.isdigit()]
    return segs[-1] if segs else occ.canonical_path


def _norm(name: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _within_one_edit(a: str, b: str) -> bool:
    """True when a and b differ by at most one insert/delete/substitution."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la > lb:
        a, b, la, lb = b, a, lb, la  # ensure a is the shorter/equal
    i = j = 0
    edited = False
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        if edited:
            return False
        edited = True
        if la == lb:
            i += 1
            j += 1  # substitution
        else:
            j += 1  # insertion into b
    return True


def suggested_extractor(producer_src: ValueOccurrence) -> tuple[ExtractorMethod, str]:
    return _extractor_for(producer_src)


def is_correlatable_source(producer_src: ValueOccurrence) -> bool:
    """Response headers we never correlate as noise (Date/Server/etc.) are still
    selectable, but we hint when a source is infrastructure noise."""
    if producer_src.location_type != LocationType.HEADER:
        return True
    return (producer_src.key or "").lower() not in _NOISE_RESPONSE_HEADERS


_NOISE_RESPONSE_HEADERS = {
    "date", "server", "connection", "content-length", "transfer-encoding",
    "content-encoding", "cf-ray", "x-powered-by", "via", "age", "keep-alive",
    "vary", "cache-control", "expires", "content-type", "accept-ranges",
}
