"""Analysis lifecycle endpoints."""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile

from ...core.config import Settings, get_settings
from ...core.errors import conflict, not_found, validation_error
from ...core.logging import get_logger
from ...domain.enums import CandidateState, Confidence, JmxStatus, RuleOrigin, RuleState
from ...domain.models import CorrelationRule
from ...repositories.analysis_store import Analysis, SessionStore
from ...schemas import presenters
from ...schemas.api import GenerateRequest, ValidateRequest
from ...services import analysis_service, jmeter_runner
from ...services.jmx_builder import BuildOptions, JmxBuilder
from ...services.jmx_validator import validate_jmx
from ...services.manifest_builder import build_manifest
from ..deps import enforce_rate_limit, get_owner_key, get_store, require_analysis

router = APIRouter(prefix="/analyses", tags=["analyses"])
log = get_logger("analyses")


@router.post("", status_code=201)
async def create_analysis(
    files: list[UploadFile] = File(...),
    settings: Settings = Depends(get_settings),
    store: SessionStore = Depends(get_store),
    owner_key: str = Depends(get_owner_key),
    _: None = Depends(enforce_rate_limit),
) -> dict:
    if not files or len(files) > settings.max_files:
        raise validation_error(f"Upload one or two Newman reports (received {len(files)}).")
    payloads: list[tuple[str, bytes]] = []
    for f in files:
        raw = await f.read()
        payloads.append((f.filename or "report.json", raw))
    analysis = analysis_service.build_analysis(payloads, settings)
    analysis.owner_key = owner_key
    store.create(analysis)
    log.info("analysis created", extra={"stage": "create", "analysis_id": analysis.id, "count": len(payloads)})
    return presenters.analysis_summary(analysis)


@router.get("/{analysis_id}")
async def get_analysis(analysis: Analysis = Depends(require_analysis)) -> dict:
    return presenters.analysis_summary(analysis)


@router.delete("/{analysis_id}", status_code=204)
async def delete_analysis(
    analysis: Analysis = Depends(require_analysis), store: SessionStore = Depends(get_store),
) -> None:
    if not store.delete(analysis.id):
        raise not_found("Analysis not found or expired.")


@router.get("/{analysis_id}/executions")
async def list_executions(
    analysis: Analysis = Depends(require_analysis),
    run: str = Query("baseline", pattern="^(baseline|comparison)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict:
    target = analysis.baseline_run if run == "baseline" else analysis.comparison_run
    if target is None:
        raise not_found("Comparison run not available in single-run mode.")
    execs = target.executions
    start = (page - 1) * page_size
    items = execs[start : start + page_size]
    return {
        "run": run,
        "page": page,
        "page_size": page_size,
        "total": len(execs),
        "items": [presenters.execution_summary(e) for e in items],
    }


@router.get("/{analysis_id}/executions/{execution_id}")
async def get_execution(
    execution_id: str, analysis: Analysis = Depends(require_analysis)
) -> dict:
    for run in (analysis.baseline_run, analysis.comparison_run):
        if run is None:
            continue
        for e in run.executions:
            if e.id == execution_id:
                return presenters.execution_detail(e)
    raise not_found("Execution not found.")


@router.get("/{analysis_id}/executions/{execution_id}/occurrences")
async def list_occurrences(
    execution_id: str,
    side: str = Query("response", pattern="^(request|response)$"),
    analysis: Analysis = Depends(require_analysis),
) -> dict:
    from ...services.value_indexer import index_request_sinks, index_response_sources

    for run in (analysis.baseline_run, analysis.comparison_run):
        if run is None:
            continue
        for e in run.executions:
            if e.id == execution_id:
                occs = index_response_sources(e) if side == "response" else index_request_sinks(e)
                return {"side": side, "items": [presenters.occurrence_dto(o) for o in occs]}
    raise not_found("Execution not found.")


@router.get("/{analysis_id}/find-consumers")
async def find_consumers_endpoint(
    producer_execution_id: str = Query(...),
    location_type: str = Query(...),
    canonical_path: str = Query(...),
    analysis: Analysis = Depends(require_analysis),
) -> dict:
    """Assisted correlation: given a value from a response, find every later
    request that uses it, and suggest the extractor + variable to wire it."""
    from ...services import consumer_finder
    from ...utils.masking import mask_if_sensitive
    from ...utils.naming import suggest_variable_name

    run = analysis.sequence_run
    found = consumer_finder.find_producer_source(run, producer_execution_id, location_type, canonical_path)
    if found is None:
        raise not_found("Producer execution not found.")
    prod_exec, src = found
    if src is None:
        raise not_found("Selected value not found in that response.")
    matches = consumer_finder.find_consumers(run, prod_exec, src)
    name_matches = consumer_finder.find_name_matches(run, prod_exec, src)
    method, expr = consumer_finder.suggested_extractor(src)
    return {
        "producer": presenters.occurrence_dto(src),
        "producer_request": prod_exec.item_name,
        "value_masked": mask_if_sensitive(src.key, src.raw_value),
        "suggested_variable": suggest_variable_name(src.key, src.canonical_path),
        "suggested_extractor": method.value,
        "suggested_expression": expr,
        "is_noise_source": not consumer_finder.is_correlatable_source(src),
        "consumers": [
            {
                **presenters.occurrence_dto(sink),
                "wrapper": wrapper,
                "request_name": e.item_name,
                "request_index": e.original_index,
            }
            for (e, sink, wrapper) in matches
        ],
        "name_matches": [
            {
                **presenters.occurrence_dto(sink),
                "wrapper": None,
                "request_name": e.item_name,
                "request_index": e.original_index,
                "producer_field": pfield,
                "consumer_field": cfield,
            }
            for (e, sink, pfield, cfield) in name_matches
        ],
    }


@router.get("/{analysis_id}/candidates")
async def list_candidates(
    analysis: Analysis = Depends(require_analysis),
    confidence: str | None = Query(None),
    extractor: str | None = Query(None),
    state: str | None = Query(None),
) -> dict:
    items = analysis.candidates
    if confidence:
        items = [c for c in items if c.confidence.value == confidence]
    if extractor:
        items = [c for c in items if c.extractor_method.value == extractor]
    if state:
        items = [c for c in items if c.state.value == state]
    return {"total": len(items), "items": [presenters.candidate_dto(c) for c in items]}


@router.get("/{analysis_id}/insights")
async def list_insights(
    analysis: Analysis = Depends(require_analysis),
    classification: str | None = Query(None),
) -> dict:
    items = analysis.insights
    if classification:
        items = [i for i in items if i.classification.value == classification]
    return {
        "total": len(items),
        "summary": analysis.summary.model_dump(mode="json"),
        "items": [presenters.insight_dto(i) for i in items],
    }


@router.get("/{analysis_id}/graph")
async def dependency_graph(analysis: Analysis = Depends(require_analysis)) -> dict:
    """Nodes (one per API) and directed producer→consumer edges for the
    dependency graph view. Edges carry variable, response key, consumer
    location, confidence and state so the client can label and filter them."""
    from ...services import graph_builder

    return graph_builder.build_graph(analysis)


@router.post("/{analysis_id}/auto-correlate")
async def auto_correlate_endpoint(
    analysis: Analysis = Depends(require_analysis),
    settings: Settings = Depends(get_settings),
) -> dict:
    """One click: correlate EVERY response value that is reused in a later
    request, automatically (no manual rule definition).

    Idempotent: once completed it returns the same result and never generates a
    duplicate extractor for the same (producer, path, variable). Re-enabled only
    when a fresh analysis is created (new upload / Reset)."""
    import time

    from ...services import auto_correlator, rule_validator

    _require_healthy_automatic_correlation(analysis)
    if analysis.auto_correlation_status == "completed" and analysis.auto_correlation_result is not None:
        # Already correlated for this analysis - return the cached outcome so a
        # double-submit (or a client retry) can never duplicate rules.
        return {**analysis.auto_correlation_result, "idempotent": True}

    analysis.auto_correlation_status = "running"
    try:
        proposals = auto_correlator.auto_correlate(analysis.sequence_run, settings)
        existing = analysis.existing_variable_names()
        by_source = {_source_key(r): r for r in analysis.rules.values()}
        claimed = {_consumer_key(c) for r in analysis.rules.values() if r.state == RuleState.ENABLED for c in r.consumers}
        created = []
        updated = []
        for r in proposals:
            prior = by_source.get(_source_key(r))
            fresh = [c for c in r.consumers if _consumer_key(c) not in claimed]
            if not fresh:
                continue
            if prior is not None:
                if prior.state != RuleState.ENABLED:
                    continue
                merged = prior.model_copy(deep=True)
                merged.consumers.extend(fresh)
                issues = rule_validator.validate_rule(
                    merged, analysis.sequence_run, analysis.sequence_run,
                    existing - {prior.variable_name}, comparison_run=analysis.comparison_run,
                )
                if not issues:
                    analysis.rules[prior.id] = merged
                    by_source[_source_key(merged)] = merged
                    claimed.update(_consumer_key(c) for c in fresh)
                    updated.append(merged)
                continue
            r.consumers = fresh
            if r.variable_name in existing:
                base, i = r.variable_name, 2
                while f"{base}_{i}" in existing:
                    i += 1
                r.variable_name = f"{base}_{i}"
            issues = rule_validator.validate_rule(
                r, analysis.sequence_run, analysis.sequence_run, existing,
                comparison_run=analysis.comparison_run,
            )
            if issues:
                continue  # never add a rule that would not validate
            analysis.rules[r.id] = r
            existing.add(r.variable_name)
            by_source[_source_key(r)] = r
            claimed.update(_consumer_key(c) for c in r.consumers)
            created.append(r)
        if created or updated:
            analysis.invalidate_generated()
        enabled = [r for r in analysis.rules.values() if r.state == RuleState.ENABLED]
        for candidate in analysis.candidates:
            needed = {_consumer_key(c) for c in candidate.consumers}
            covered = {_consumer_key(c) for rule in enabled
                       if rule.producer.raw_value == candidate.producer.raw_value for c in rule.consumers}
            if needed and needed <= covered:
                candidate.state = CandidateState.ACCEPTED
        result = {
            "created": len(created),
            "updated": len(updated),
            "total": len(enabled),
            "variables": [r.variable_name for r in enabled],
            "rules": [presenters.rule_dto(r) for r in enabled],
        }
        analysis.auto_correlation_status = "completed"
        analysis.auto_correlated_at = time.time()
        analysis.auto_correlation_result = result
    except Exception:
        analysis.auto_correlation_status = "failed"
        raise
    log.info("auto-correlate", extra={"stage": "auto", "analysis_id": analysis.id, "rule_count": len(created)})
    return result


@router.post("/{analysis_id}/candidates/{candidate_id}/accept")
async def accept_candidate(
    candidate_id: str, analysis: Analysis = Depends(require_analysis)
) -> dict:
    rule = _accept_candidate(analysis, candidate_id)
    if rule is None:
        raise not_found("Candidate not found.")
    return presenters.rule_dto(rule)


@router.post("/{analysis_id}/candidates/accept-high")
async def accept_high(analysis: Analysis = Depends(require_analysis)) -> dict:
    _require_healthy_automatic_correlation(analysis)
    accepted = []
    for c in analysis.candidates:
        if c.confidence == Confidence.HIGH and c.state == CandidateState.SUGGESTED:
            rule = _accept_candidate(analysis, c.id)
            if rule:
                accepted.append(rule)
    return {"accepted": len(accepted), "rules": [presenters.rule_dto(r) for r in accepted]}


@router.post("/{analysis_id}/candidates/{candidate_id}/reject")
async def reject_candidate(
    candidate_id: str, analysis: Analysis = Depends(require_analysis)
) -> dict:
    for c in analysis.candidates:
        if c.id == candidate_id:
            c.state = CandidateState.REJECTED
            return {"id": candidate_id, "state": c.state.value}
    raise not_found("Candidate not found.")


@router.post("/{analysis_id}/preview")
async def preview(
    body: GenerateRequest | None = None,
    analysis: Analysis = Depends(require_analysis),
) -> dict:
    manifest, _, validation = _generate(analysis, body or GenerateRequest(), persist=False)
    return {
        "status": JmxStatus.DRAFT.value,
        "manifest": manifest,
        "validation": {"ok": validation.ok, "errors": validation.errors, "warnings": validation.warnings},
        "blocked": presenters._is_blocked(analysis),
        "dependency_flow": manifest["dependency_flow"],
    }


@router.post("/{analysis_id}/generate")
async def generate(
    body: GenerateRequest | None = None,
    analysis: Analysis = Depends(require_analysis),
) -> dict:
    manifest, xml, validation = _generate(analysis, body or GenerateRequest(), persist=True)
    if not validation.ok:
        raise validation_error(
            "Generated JMX failed structural validation.",
            errors=[{"path": "jmx", "detail": e} for e in validation.errors],
        )
    return {
        "status": JmxStatus.GENERATED.value,
        "note": (
            "This is a Generated JMX (structurally valid). It becomes a Validated JMX only after "
            "Apache JMeter 5.6.3 executes it successfully."
        ),
        "manifest": manifest,
        "validation": {"ok": validation.ok, "errors": validation.errors, "warnings": validation.warnings},
        "blocked": presenters._is_blocked(analysis),
        "download": {
            "jmx": f"/api/v1/analyses/{analysis.id}/download/jmx",
            "manifest": f"/api/v1/analyses/{analysis.id}/download/manifest",
        },
    }


@router.post("/{analysis_id}/validate")
async def validate(
    body: ValidateRequest | None = None,
    analysis: Analysis = Depends(require_analysis),
    settings: Settings = Depends(get_settings),
) -> dict:
    if not analysis.generated_jmx:
        raise validation_error("No JMX has been generated yet. Call generate first.")
    body = body or ValidateRequest()
    manifest = analysis.generated_manifest or {}
    correlation_variables = list(manifest.get("variables") or [])
    required_properties = list((manifest.get("secret_handling") or {}).get("required_properties") or [])
    plan = analysis.generated_jmx

    # run_plan shells out to JMeter and blocks synchronously for the full run;
    # off-load it so a slow/remote validation can't freeze the event loop.
    report = await asyncio.to_thread(
        jmeter_runner.run_plan,
        plan,
        correlation_variables=correlation_variables,
        required_properties=required_properties,
        property_values=body.properties,
        settings=settings,
    )
    if analysis.generated_jmx != plan or analysis.generated_manifest is not manifest:
        raise conflict("The plan changed during validation. Generate and validate the current plan again.")
    analysis.jmx_status = report.status
    analysis.validation_report = report.model_dump(mode="json")
    log.info(
        "jmx validated",
        extra={"stage": "validate", "analysis_id": analysis.id, "status": report.status},
    )
    return {
        "status": report.status,
        "jmeter_available": jmeter_runner.jmeter_available(settings),
        "report": analysis.validation_report,
    }


@router.get("/{analysis_id}/validation")
async def get_validation(analysis: Analysis = Depends(require_analysis)) -> dict:
    return {
        "jmx_status": analysis.jmx_status,
        "jmeter_available": jmeter_runner.jmeter_available(get_settings()),
        "report": analysis.validation_report,
    }


# --- internals ---


def _require_healthy_automatic_correlation(analysis: Analysis) -> None:
    if presenters._is_blocked(analysis):
        raise validation_error(
            "Run health blocks automatic correlation. Fix the errors shown in Run health "
            "and capture two successful runs before trying again."
        )

def _source_key(rule):
    p = rule.producer
    return p.execution_id, p.location_type, p.canonical_path


def _consumer_key(consumer):
    return consumer.execution_id, consumer.location_type, consumer.canonical_path

def _accept_candidate(analysis: Analysis, candidate_id: str) -> CorrelationRule | None:
    _require_healthy_automatic_correlation(analysis)
    for c in analysis.candidates:
        if c.id != candidate_id:
            continue
        needed = {_consumer_key(sink) for sink in c.consumers}
        for existing in analysis.rules.values():
            covered = {_consumer_key(sink) for sink in existing.consumers}
            if existing.state == RuleState.ENABLED and needed and needed <= covered and existing.producer.raw_value == c.producer.raw_value:
                c.state = CandidateState.ACCEPTED
                return existing
            if existing.state == RuleState.ENABLED and needed & covered and _source_key(existing) != (c.producer.execution_id, c.producer.location_type, c.producer.canonical_path):
                raise validation_error("A consumer is already assigned to another correlation rule. Edit that rule instead.")
        c.state = CandidateState.ACCEPTED
        for existing in analysis.rules.values():
            if _source_key(existing) == (c.producer.execution_id, c.producer.location_type, c.producer.canonical_path):
                existing.state = RuleState.ENABLED
                known = {_consumer_key(sink) for sink in existing.consumers}
                existing.consumers.extend(sink for sink in c.consumers if _consumer_key(sink) not in known)
                existing.source_candidate_id = c.id
                analysis.invalidate_generated()
                return existing
        from ...utils.naming import dedupe_variable_name

        rule = CorrelationRule(
            id=uuid.uuid4().hex,
            variable_name=dedupe_variable_name(c.variable_name, analysis.existing_variable_names()),
            origin=RuleOrigin.AUTOMATIC,
            state=RuleState.ENABLED,
            producer=c.producer,
            consumers=list(c.consumers),
            extractor_method=c.extractor_method,
            extractor_expression=c.extractor_expression,
            match_number=c.match_number,
            confidence=c.confidence,
            evidence=c.evidence,
            warnings=list(c.warnings),
            source_candidate_id=c.id,
        )
        analysis.rules[rule.id] = rule
        analysis.invalidate_generated()
        return rule
    return None


def _generate(analysis: Analysis, body: GenerateRequest, *, persist: bool):
    options = BuildOptions(
        num_threads=body.num_threads,
        loops=body.loops,
        parameterize_host=body.parameterize_host,
        include_cache_manager=body.include_cache_manager,
        include_static_secrets=body.include_static_secrets,
        keep_user_agent=body.keep_user_agent,
    )
    rules = list(analysis.rules.values())
    builder = JmxBuilder(options)
    result = builder.build(analysis.sequence_run, rules, comparison_run=analysis.comparison_run)
    validation = validate_jmx(result.xml)
    for error in result.errors:
        validation.fail(error)

    # Generation-time materialization check: an accepted correlation must appear
    # as ${var} in the rendered request. If the generator skipped a replacement
    # (e.g. a JSON body left with the literal value), block the download so the
    # UI never claims "correlated" when the JMX still holds the literal value.
    for u in result.unmaterialized:
        validation.fail(
            f"Accepted correlation '${{{u['variable']}}}' for {u['consumer']} "
            f"({u['location']}:{u['path']}) was not written into the generated request."
        )

    warnings: list[str] = list(analysis.warnings) + result.warnings
    if analysis.comparison_run and analysis.comparison_run.health.blockers:
        warnings.extend(analysis.comparison_run.health.blockers)
    if analysis.baseline_run.health.blockers:
        warnings.extend(analysis.baseline_run.health.blockers)

    manifest = build_manifest(
        analysis.sequence_run,
        rules,
        result,
        candidates_found=len(analysis.candidates),
        manual_added=sum(1 for r in rules if r.origin == RuleOrigin.MANUAL),
        edited=analysis.edited_count,
        deleted=analysis.deleted_count,
        warnings=warnings,
    )
    if persist and validation.ok:
        analysis.validation_report = None
        analysis.generated_jmx = result.xml
        analysis.generated_manifest = manifest
        analysis.jmx_status = JmxStatus.GENERATED.value
    return manifest, result.xml, validation
