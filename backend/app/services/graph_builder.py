"""Build the API dependency graph (nodes + producer→consumer edges).

The graph is a *view* over data the engine already produced: every execution in
the authoritative sequence run is a node, and every proven producer→consumer
relationship (from a detected candidate or an accepted/manual rule) is a
directed edge. Layout is intentionally left to the client so it can recompute
positions when the user collapses folders or enters focus mode.

Edges never point backwards: a producer always precedes its consumers in the
sequence run, matching the correlation engine's own invariant.
"""

from __future__ import annotations

from ..domain.enums import RuleOrigin, RuleState
from ..domain.models import NormalizedExecution, NormalizedRun, ValueOccurrence
from ..repositories.analysis_store import Analysis
from ..utils.masking import mask_if_sensitive


def _folder(e: NormalizedExecution) -> str:
    # item_path includes the request name as its last element; the folder is the
    # containing collection path. Fall back to a stable root label.
    parent = e.item_path[:-1] if len(e.item_path) > 1 else e.item_path[:0]
    return " / ".join(parent) if parent else "(root)"


def _short_path(e: NormalizedExecution) -> str:
    path = e.request.path or "/"
    return path if len(path) <= 48 else path[:45] + "…"


def _response_key(producer: ValueOccurrence) -> str:
    if producer.key:
        return producer.key
    seg = producer.canonical_path.rstrip("]").replace("[", ".").split(".")
    return seg[-1] if seg and seg[-1] else producer.canonical_path


def build_graph(analysis: Analysis) -> dict:
    run: NormalizedRun = analysis.sequence_run
    by_id: dict[str, NormalizedExecution] = {e.id: e for e in run.executions}

    # --- collect correlation sources ---------------------------------------
    # Both detected candidates (suggested/accepted/rejected) and every enabled
    # rule are sources. An enabled rule may come from accepting a candidate
    # (source_candidate_id), from one-click auto-correlate (origin AUTOMATIC, no
    # candidate) or from a manual rule. We overlay them and dedupe by edge
    # identity, keeping the strongest state, so an accepted/auto rule is never
    # left looking merely "suggested" and never drawn twice.
    accepted_candidate_ids = {
        r.source_candidate_id
        for r in analysis.rules.values()
        if r.state == RuleState.ENABLED and r.source_candidate_id
    }

    sources: list[dict] = []
    enabled_sources = {
        (r.producer.execution_id, r.producer.location_type, r.producer.canonical_path)
        for r in analysis.rules.values() if r.state == RuleState.ENABLED
    }
    enabled_consumers = {
        (sink.execution_id, sink.location_type, sink.canonical_path)
        for rule in analysis.rules.values() if rule.state == RuleState.ENABLED
        for sink in rule.consumers
    }
    for c in analysis.candidates:
        if (c.producer.execution_id, c.producer.location_type, c.producer.canonical_path) in enabled_sources:
            continue  # the enabled rule supplies the actual name and all consumers
        remaining = [sink for sink in c.consumers if
                     (sink.execution_id, sink.location_type, sink.canonical_path) not in enabled_consumers]
        if not remaining:
            continue
        state = "accepted" if c.id in accepted_candidate_ids else c.state.value
        sources.append(
            {
                "variable": c.variable_name,
                "producer": c.producer,
                "consumers": remaining,
                "confidence": c.confidence.value,
                "classification": c.classification.value,
                "state": state,
            }
        )
    for r in analysis.rules.values():
        if r.state != RuleState.ENABLED:
            continue
        sources.append(
            {
                "variable": r.variable_name,
                "producer": r.producer,
                "consumers": r.consumers,
                "confidence": r.confidence.value,
                "classification": "correlation",
                "state": "manual" if r.origin == RuleOrigin.MANUAL else "accepted",
            }
        )

    # Build edges keyed by identity; on collision keep the stronger state.
    state_rank = {"rejected": 0, "suggested": 1, "accepted": 2, "manual": 2}
    edge_map: dict[str, dict] = {}
    for src in sources:
        producer: ValueOccurrence = src["producer"]
        prod_id = producer.execution_id
        if prod_id not in by_id:
            continue
        response_key = _response_key(producer)
        for sink in src["consumers"]:
            cons_id = sink.execution_id
            if cons_id not in by_id:
                continue
            eid = f"{prod_id}:{cons_id}:{src['variable']}:{sink.location_type.value}:{sink.canonical_path}"
            edge = {
                "id": eid,
                "source": prod_id,
                "target": cons_id,
                "variable": src["variable"],
                "response_key": response_key,
                "producer_path": producer.canonical_path,
                "consumer_location": sink.location_type.value,
                "consumer_path": sink.canonical_path,
                "wrapper": sink.wrapper,
                "value_masked": mask_if_sensitive(producer.key, producer.raw_value),
                "confidence": src["confidence"],
                "classification": src["classification"],
                "state": src["state"],
            }
            prev = edge_map.get(eid)
            if prev is None or state_rank[src["state"]] > state_rank[prev["state"]]:
                edge_map[eid] = edge

    edges = list(edge_map.values())
    produces: dict[str, set[str]] = {}
    consumes: dict[str, set[str]] = {}
    variables: set[str] = set()
    locations: set[str] = set()
    confidences: set[str] = set()
    for e in edges:
        produces.setdefault(e["source"], set()).add(e["variable"])
        consumes.setdefault(e["target"], set()).add(e["variable"])
        variables.add(e["variable"])
        locations.add(e["consumer_location"])
        confidences.add(e["confidence"])

    nodes: list[dict] = []
    folders: set[str] = set()
    for e in run.executions:
        folder = _folder(e)
        folders.add(folder)
        prod = sorted(produces.get(e.id, set()))
        cons = sorted(consumes.get(e.id, set()))
        if prod and cons:
            role = "both"
        elif prod:
            role = "producer"
        elif cons:
            role = "consumer"
        else:
            role = "none"
        nodes.append(
            {
                "id": e.id,
                "index": e.original_index,
                "name": e.item_name,
                "method": e.method,
                "path": _short_path(e),
                "folder": folder,
                "status_code": e.response.code if e.response is not None else None,
                "role": role,
                "produces": prod,
                "consumes": cons,
                "correlation_count": len(prod) + len(cons),
            }
        )

    return {
        "nodes": nodes,
        "edges": edges,
        "facets": {
            "variables": sorted(variables),
            "folders": sorted(folders),
            "locations": sorted(locations),
            "confidence_levels": [c for c in ("high", "medium", "low") if c in confidences],
        },
        "stats": {
            "apis": len(nodes),
            "producers": sum(1 for n in nodes if n["role"] in ("producer", "both")),
            "consumers": sum(1 for n in nodes if n["role"] in ("consumer", "both")),
            "variables": len(variables),
            "edges": len(edges),
        },
    }
