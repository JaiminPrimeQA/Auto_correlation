"""Manual correlation rule endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from ...core.errors import not_found, validation_error
from ...domain.enums import RuleOrigin, RuleState
from ...domain.models import CorrelationRule, NormalizedExecution, ValueOccurrence
from ...repositories.analysis_store import Analysis
from ...schemas import presenters
from ...schemas.api import OccurrenceRef, RuleCreateRequest, RuleUpdateRequest
from ...services import rule_validator
from ...services.value_indexer import index_request_sinks, index_response_sources
from ..deps import require_analysis

router = APIRouter(prefix="/analyses/{analysis_id}/rules", tags=["rules"])


def _find_execution(analysis: Analysis, execution_id: str) -> NormalizedExecution | None:
    for e in analysis.sequence_run.executions:
        if e.id == execution_id:
            return e
    return None


def _resolve_producer(analysis: Analysis, ref: OccurrenceRef) -> ValueOccurrence:
    exec_ = _find_execution(analysis, ref.execution_id)
    if exec_ is None:
        raise validation_error("Producer execution not found in the sequence run.")
    for src in index_response_sources(exec_):
        if src.location_type == ref.location_type and src.canonical_path == ref.canonical_path:
            return src
    raise validation_error(
        "Producer value/location not found in that response.",
        errors=[{"code": "value_not_in_response", "detail": "No matching response source."}],
    )


def _resolve_consumer(analysis: Analysis, ref: OccurrenceRef) -> ValueOccurrence:
    exec_ = _find_execution(analysis, ref.execution_id)
    if exec_ is None:
        raise validation_error("Consumer execution not found in the sequence run.")
    for sink in index_request_sinks(exec_):
        if sink.location_type == ref.location_type and sink.canonical_path == ref.canonical_path:
            return sink.model_copy(update={"wrapper": ref.wrapper})
    raise validation_error(
        "Consumer location not found in that request.",
        errors=[{"code": "consumer_location_missing", "detail": "No matching request sink."}],
    )


@router.post("", status_code=201)
async def create_rule(
    body: RuleCreateRequest, analysis: Analysis = Depends(require_analysis)
) -> dict:
    producer = _resolve_producer(analysis, body.producer)
    consumers = [_resolve_consumer(analysis, c) for c in body.consumers]
    rule = CorrelationRule(
        id=uuid.uuid4().hex,
        variable_name=body.variable_name,
        origin=RuleOrigin.MANUAL,
        state=RuleState.ENABLED,
        producer=producer,
        consumers=consumers,
        extractor_method=body.extractor_method,
        extractor_expression=body.extractor_expression,
        match_number=body.match_number,
        default_value=body.default_value,
        expert_override=body.expert_override,
    )
    issues = rule_validator.validate_rule(
        rule, analysis.sequence_run, analysis.sequence_run,
        analysis.existing_variable_names(),
        comparison_run=analysis.comparison_run,
    )
    hard = [i for i in issues if not rule.expert_override]
    if hard:
        raise validation_error(
            "Rule failed validation.",
            errors=[{"code": i.code, "detail": i.detail} for i in hard],
        )
    analysis.rules[rule.id] = rule
    analysis.invalidate_generated()
    return presenters.rule_dto(rule)


@router.patch("/{rule_id}")
async def update_rule(
    rule_id: str, body: RuleUpdateRequest, analysis: Analysis = Depends(require_analysis)
) -> dict:
    original = analysis.rules.get(rule_id)
    if original is None:
        raise not_found("Rule not found.")
    rule = original.model_copy(deep=True)
    if body.variable_name is not None:
        rule.variable_name = body.variable_name
    if body.extractor_method is not None:
        rule.extractor_method = body.extractor_method
    if body.extractor_expression is not None:
        rule.extractor_expression = body.extractor_expression
    if body.match_number is not None:
        rule.match_number = body.match_number
    if body.default_value is not None:
        rule.default_value = body.default_value
    if body.expert_override is not None:
        rule.expert_override = body.expert_override
    if body.consumers is not None:
        rule.consumers = [_resolve_consumer(analysis, c) for c in body.consumers]
    if body.enabled is not None:
        rule.state = RuleState.ENABLED if body.enabled else RuleState.DISABLED
    issues = rule_validator.validate_rule(
        rule, analysis.sequence_run, analysis.sequence_run,
        analysis.existing_variable_names(exclude=rule_id), comparison_run=analysis.comparison_run,
    )
    if issues and not rule.expert_override:
        raise validation_error("Rule failed validation.", errors=[{"code": i.code, "detail": i.detail} for i in issues])
    analysis.rules[rule_id] = rule
    analysis.invalidate_generated()
    analysis.edited_count += 1
    return presenters.rule_dto(rule)


@router.delete("/{rule_id}", status_code=204)
async def delete_rule(rule_id: str, analysis: Analysis = Depends(require_analysis)) -> None:
    if rule_id not in analysis.rules:
        raise not_found("Rule not found.")
    analysis.rules.pop(rule_id)
    analysis.invalidate_generated()
    analysis.deleted_count += 1


@router.post("/{rule_id}/validate")
async def validate_rule_endpoint(
    rule_id: str, analysis: Analysis = Depends(require_analysis)
) -> dict:
    rule = analysis.rules.get(rule_id)
    if rule is None:
        raise not_found("Rule not found.")
    issues = rule_validator.validate_rule(
        rule, analysis.sequence_run, analysis.sequence_run,
        analysis.existing_variable_names(exclude=rule_id),
        comparison_run=analysis.comparison_run,
    )
    producer_exec = _find_execution(analysis, rule.producer.execution_id)
    resolved = None
    if producer_exec is not None:
        resolved = rule_validator.resolve_extractor(
            producer_exec, rule.extractor_method, rule.extractor_expression
        )
    from ...utils.masking import mask_value

    return {
        "valid": len(issues) == 0,
        "issues": [{"code": i.code, "detail": i.detail} for i in issues],
        "resolved_value_masked": mask_value(resolved) if resolved is not None else None,
    }
