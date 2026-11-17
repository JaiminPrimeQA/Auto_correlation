"""Presenters that build masked, transport-safe response payloads."""

from __future__ import annotations

import json

from ..domain.enums import Side
from ..domain.execution_job import ExecutionJob
from ..domain.models import (
    AlignmentReport,
    CorrelationCandidate,
    CorrelationRule,
    NormalizedExecution,
    NormalizedRequest,
    Pair,
    RunHealth,
    ValueInsight,
    ValueOccurrence,
)
from ..domain.postman_models import CollectionInspection, PostmanFolder, PostmanVariable, UnsupportedFeature
from ..repositories.analysis_store import Analysis
from ..services.har_import import Decision, HarRecording
from ..utils.masking import is_credential_field, is_credential_name, mask_if_sensitive, mask_value


def _occurrence_value(occ: ValueOccurrence) -> str:
    field = occ.key or occ.canonical_path.rsplit(".", 1)[-1]
    if occ.side == Side.REQUEST and is_credential_name(field):
        return mask_value(occ.raw_value)
    return mask_if_sensitive(occ.key, occ.raw_value)


def occurrence_dto(occ: ValueOccurrence) -> dict:
    return {
        "execution_id": occ.execution_id,
        "execution_index": occ.execution_index,
        "side": occ.side.value,
        "location_type": occ.location_type.value,
        "canonical_path": occ.canonical_path,
        "key": occ.key,
        "value_masked": _occurrence_value(occ),
        "data_type": occ.data_type.value,
        "wrapper": occ.wrapper,
    }


def candidate_dto(c: CorrelationCandidate) -> dict:
    return {
        "id": c.id,
        "variable_name": c.variable_name,
        "producer": occurrence_dto(c.producer),
        "consumers": [occurrence_dto(x) for x in c.consumers],
        "consumer_count": len(c.consumers),
        "extractor_method": c.extractor_method.value,
        "extractor_expression": c.extractor_expression,
        "match_number": c.match_number,
        "classification": c.classification.value,
        "confidence": c.confidence.value,
        "evidence": {
            "score": c.evidence.score,
            "factors": c.evidence.factors,
            "penalties": c.evidence.penalties,
            "baseline_value_masked": c.evidence.baseline_value_masked,
            "comparison_value_masked": c.evidence.comparison_value_masked,
        },
        "warnings": c.warnings,
        "state": c.state.value,
    }


def rule_dto(r: CorrelationRule) -> dict:
    return {
        "id": r.id,
        "variable_name": r.variable_name,
        "origin": r.origin.value,
        "state": r.state.value,
        "producer": occurrence_dto(r.producer),
        "consumers": [occurrence_dto(x) for x in r.consumers],
        "extractor_method": r.extractor_method.value,
        "extractor_expression": r.extractor_expression,
        "match_number": r.match_number,
        "default_value": r.default_value,
        "confidence": r.confidence.value,
        "warnings": r.warnings,
        "expert_override": r.expert_override,
    }


def insight_dto(i: ValueInsight) -> dict:
    return {
        "id": i.id,
        "classification": i.classification.value,
        "handling": i.handling.value,
        "variable_name": i.variable_name,
        "location": occurrence_dto(i.location),
        "occurrence_count": len(i.occurrences),
        "occurrences": [occurrence_dto(o) for o in i.occurrences],
        "value_a_masked": i.value_a_masked,
        "value_b_masked": i.value_b_masked,
        "differs_across_runs": i.differs_across_runs,
        "reason": i.reason,
        "recommended_handling": i.recommended_handling,
        "warnings": i.warnings,
    }


def health_dto(h: RunHealth) -> dict:
    return h.model_dump(mode="json")


def _pairs_dto(pairs: list[Pair], *, mask: bool) -> list[dict]:
    return [
        {"name": p.name, "value": mask_if_sensitive(p.name, p.value) if mask else p.value}
        for p in pairs
    ]


def _mask_credentials(node: object, path: str) -> object:
    if isinstance(node, dict):
        return {k: mask_value(v) if isinstance(v, str) and is_credential_field(k, path) else _mask_credentials(v, path)
                for k, v in node.items()}
    if isinstance(node, list):
        return [_mask_credentials(v, path) for v in node]
    return node


def _display_body(req: NormalizedRequest) -> str | None:
    masked = _mask_credentials(req.parsed_body, req.path)
    return req.raw_body if masked == req.parsed_body else json.dumps(masked)


def _display_form(req: NormalizedRequest) -> list[dict]:
    return [
        {"name": p.name, "value": mask_value(p.value) if is_credential_field(p.name, req.path)
         else mask_if_sensitive(p.name, p.value)}
        for p in req.form_data
    ]


def execution_summary(e: NormalizedExecution) -> dict:
    return {
        "id": e.id,
        "index": e.original_index,
        "name": e.item_name,
        "method": e.method,
        "url": e.normalized_url,
        "status_code": e.response.code if e.response is not None else None,
        "has_response": e.has_usable_response,
        "item_path": e.item_path,
    }


def execution_detail(e: NormalizedExecution) -> dict:
    req = e.request
    resp = e.response
    return {
        "id": e.id,
        "index": e.original_index,
        "name": e.item_name,
        "request": {
            "method": req.method,
            "url": req.raw_url,
            "protocol": req.protocol,
            "host": req.host,
            "port": req.port,
            "path": req.path,
            "query": _pairs_dto(req.query, mask=True),
            "headers": _pairs_dto(req.headers, mask=True),
            "cookies": _pairs_dto(req.cookies, mask=True),
            "body_mode": req.body_mode.value,
            "raw_body": _display_body(req),
            "form_data": _display_form(req),
            "content_type": req.content_type,
        },
        "response": None if resp is None else {
            "status": resp.status,
            "code": resp.code,
            "content_type": resp.content_type,
            "headers": _pairs_dto(resp.headers, mask=True),
            "cookies": _pairs_dto(resp.cookies, mask=True),
            "raw_body": resp.raw_body_text,
            "body_size": resp.body_size,
            "decoding_warnings": resp.decoding_warnings,
        },
        "assertions": [a.model_dump(mode="json") for a in e.assertions],
    }


def alignment_dto(a: AlignmentReport | None) -> dict | None:
    if a is None:
        return None
    return a.model_dump(mode="json")


def analysis_summary(analysis: Analysis) -> dict:
    return {
        "analysis_id": analysis.id,
        "mode": analysis.mode.value,
        "baseline_health": health_dto(analysis.baseline_run.health),
        "comparison_health": health_dto(analysis.comparison_run.health) if analysis.comparison_run else None,
        "alignment": alignment_dto(analysis.alignment),
        "candidate_count": len(analysis.candidates),
        # Still waiting for accept/reject (what the Candidates tab lists).
        "open_candidate_count": sum(1 for c in analysis.candidates if c.state.value == "suggested"),
        "summary": analysis.summary.model_dump(mode="json"),
        "insight_count": len(analysis.insights),
        "readiness": analysis.readiness.model_dump(mode="json"),
        "jmx_status": analysis.jmx_status,
        "auto_correlation_status": analysis.auto_correlation_status,
        "rule_count": len([r for r in analysis.rules.values()]),
        "warnings": analysis.warnings
        + analysis.baseline_run.warnings
        + (analysis.comparison_run.warnings if analysis.comparison_run else []),
        "blocked": _is_blocked(analysis),
        "collection_name": analysis.baseline_run.collection_name,
        "source": analysis.source,
    }


def _is_blocked(analysis: Analysis) -> bool:
    if analysis.comparison_run and analysis.comparison_run.health.blockers:
        return True
    if analysis.baseline_run.health.blockers:
        return True
    return False


def har_recording_dto(recording: HarRecording) -> dict:
    counts = recording.counts()
    return {
        "filename": recording.filename,
        "total": len(recording.reviewed),
        "kept": counts[Decision.KEEP.value],
        "optional": counts[Decision.OPTIONAL.value],
        "removed": counts[Decision.REMOVED.value],
        "size_bytes": recording.size_bytes,
        "cleaned_bytes": recording.cleaned_bytes(recording.default_selection()),
        "removed_by_reason": recording.removed_by_reason(),
        "warnings": recording.warnings,
        "entries": [
            {"index": r.index, "method": r.method, "url": r.url, "status": r.status, "decision": r.decision.value,
             "reason": r.reason}
            for r in recording.reviewed if r.decision != Decision.REMOVED
        ],
    }


def postman_variable_dto(v: PostmanVariable) -> dict:
    return {"name": v.name, "source": v.source.value, "sensitive": v.sensitive, "locations": v.locations}


def postman_folder_dto(f: PostmanFolder) -> dict:
    return {"id": f.id, "name": f.name, "path": f.path, "request_count": f.request_count}


def postman_unsupported_feature_dto(u: UnsupportedFeature) -> dict:
    return {"kind": u.kind.value, "detail": u.detail, "location": u.location}


def postman_inspection_dto(inspection: CollectionInspection) -> dict:
    return {
        "collection_name": inspection.collection_name,
        "folders": [postman_folder_dto(f) for f in inspection.folders],
        "variables": [postman_variable_dto(v) for v in inspection.variables],
        "unresolved_variable_names": inspection.unresolved_variable_names,
        "request_count_estimate": inspection.request_count_estimate,
        "target_domains": inspection.target_domains,
        "domain_warnings": inspection.domain_warnings,
        "unsupported_features": [postman_unsupported_feature_dto(u) for u in inspection.unsupported_features],
        "warnings": inspection.warnings,
    }


def execution_job_dto(job: ExecutionJob) -> dict:
    return {
        "job_id": job.id,
        "state": job.state.value,
        # Copies: the DTO must not alias the stored job's mutable lists.
        "stage_history": list(job.stage_history),
        "warnings": list(job.warnings),
        "error_code": job.error_code,
        "error_detail": job.error_detail,
        "analysis_id": job.analysis_id,
    }
