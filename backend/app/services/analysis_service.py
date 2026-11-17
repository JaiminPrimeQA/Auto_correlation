"""Orchestrate the analysis pipeline: parse → normalize → health → align → correlate."""

from __future__ import annotations

import time
from urllib.parse import unquote_plus

from ..core.config import Settings
from ..core.errors import validation_error
from ..core.security import new_analysis_id
from ..domain.enums import AnalysisMode
from ..domain.models import NormalizedRun
from ..repositories.analysis_store import Analysis
from . import (
    business_health,
    classification,
    har_import,
    newman_parser,
    normalizer,
    run_aligner,
    run_health,
)
from .correlation_engine import CorrelationEngine


def build_analysis(files: list[tuple[str, bytes]], settings: Settings) -> Analysis:
    if not files:
        raise validation_error("At least one Newman report is required.")
    if len(files) > settings.max_files:
        raise validation_error(f"At most {settings.max_files} files are accepted.")
    duplicate_capture = len(files) == 2 and files[0][1] == files[1][1]

    runs = []
    for filename, raw in files:
        parsed = newman_parser.parse_report(raw, filename=filename, settings=settings)
        runs.append(_to_run(parsed.data, filename, parsed.warnings, settings))

    analysis = _analyze_runs(runs, settings)
    if duplicate_capture:
        analysis.warnings.append(
            "The two uploaded reports are identical copies of one capture. "
            "Capture a fresh second Newman run to compare dynamic values."
        )
    return analysis


def build_har_analysis(
    recordings: list[har_import.HarRecording], selections: list[set[int]], settings: Settings,
) -> Analysis:
    """`selections[i]` is the set of entry indexes chosen from `recordings[i]`."""
    runs = [
        _to_run(har_import.to_report(recording, selected), recording.filename, [], settings)
        for recording, selected in zip(recordings, selections, strict=True)
    ]
    for run in runs:
        _decode_query_values(run)
    analysis = _analyze_runs(runs, settings)
    analysis.source = "har"
    analysis.warnings.extend(dict.fromkeys(w for r in recordings for w in r.warnings))
    if len(recordings) == 2 and recordings[0].entries == recordings[1].entries:
        analysis.warnings.append(
            "The two uploaded recordings are identical copies. Record the flow again to compare dynamic values."
        )
    return analysis


def _decode_query_values(run: NormalizedRun) -> None:
    """A browser URL-encodes queries, so ab%2Fcd must match the response's ab/cd.
    Plans from a recording encode them again at run time."""
    for execution in run.executions:
        for pair in execution.request.query:
            pair.name, pair.value = unquote_plus(pair.name), unquote_plus(pair.value)


def _to_run(data: dict, filename: str, warnings: list[str], settings: Settings) -> NormalizedRun:
    run = normalizer.normalize_run(data, filename=filename, settings=settings)
    run.warnings.extend(warnings)
    run.health = run_health.compute_health(run, settings)
    return run


def _analyze_runs(runs: list[NormalizedRun], settings: Settings) -> Analysis:
    now = time.time()
    analysis_id = new_analysis_id()
    engine = CorrelationEngine(settings)

    if len(runs) == 2:
        baseline, comparison = runs
        alignment = run_aligner.align_runs(baseline, comparison)
        run_health.apply_alignment_health(baseline.health, comparison.health, alignment, settings)
        candidates = engine.analyze_two_run(baseline, comparison, alignment)
        classified = classification.classify(baseline, comparison, alignment, candidates, settings)
        scenario_warnings = business_health.detect_scenario_inconsistencies(baseline, comparison, alignment)
        readiness = business_health.compute_readiness(
            baseline, comparison, alignment, classified.summary, scenario_warnings
        )
        analysis = Analysis(
            id=analysis_id,
            mode=AnalysisMode.TWO_RUN,
            created_at=now,
            expires_at=now + settings.session_ttl_seconds,
            baseline_run=baseline,
            comparison_run=comparison,
            alignment=alignment,
            candidates=classified.correlations,
            insights=classified.insights,
            summary=classified.summary,
            readiness=readiness,
        )
    else:
        run = runs[0]
        alignment = run_aligner.single_run_report(run)
        candidates = engine.analyze_single_run(run)
        classified = classification.classify(run, None, None, candidates, settings)
        readiness = business_health.compute_readiness(run, None, None, classified.summary, [])
        analysis = Analysis(
            id=analysis_id,
            mode=AnalysisMode.SINGLE_RUN,
            created_at=now,
            expires_at=now + settings.session_ttl_seconds,
            baseline_run=run,
            comparison_run=None,
            alignment=alignment,
            candidates=classified.correlations,
            insights=classified.insights,
            summary=classified.summary,
            readiness=readiness,
            warnings=["Single-run mode: correlations are lower confidence and cannot be proven dynamic."],
        )
    return analysis
