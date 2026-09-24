# backend/app/services/execution_job_service.py
"""Create and drive execution jobs through their state machine.

`create_job` is synchronous: it parses, resolves, and gates a submission
before ever persisting a job, so a rejected submission never creates job
state a client would need to clean up. `run_job` (a later task) does the
actual state-machine driving via an injected NewmanRunner.
"""

from __future__ import annotations

import time

from ..core.config import Settings
from ..core.errors import ProblemException, rate_limited, validation_error
from ..core.security import new_analysis_id
from ..domain.execution_job import ExecutionJob, ExecutionJobState, NewmanRunner, RunInput, RunOutcome
from ..repositories.analysis_store import SessionStore
from ..repositories.execution_job_store import ExecutionJobStore
from . import analysis_service, destination_policy, postman_domain_extractor
from .postman_folder_extractor import extract_folders
from .postman_parser import parse_collection, parse_environment
from .postman_variable_extractor import extract_variable_references
from .postman_variable_resolver import collection_variable_values, resolve_variables, unresolved_names


def create_job(
    *,
    collection_raw: bytes,
    collection_filename: str,
    environment_raw: bytes | None,
    environment_filename: str | None,
    folder_id: str | None,
    supplied_values: dict[str, str],
    owner_key: str,
    idempotency_key: str | None,
    store: ExecutionJobStore,
    settings: Settings,
) -> tuple[ExecutionJob, bool]:
    if idempotency_key:
        existing = store.find_by_idempotency_key(
            owner_key, idempotency_key, window_seconds=settings.idempotency_window_seconds,
        )
        if existing is not None:
            return existing, False

    if store.count_active(owner_key) >= settings.max_concurrent_jobs_per_owner:
        raise rate_limited(
            f"Too many active execution jobs ({settings.max_concurrent_jobs_per_owner} allowed at a time)."
        )

    parsed_collection = parse_collection(collection_raw, filename=collection_filename, settings=settings)
    collection_data = parsed_collection.data

    if folder_id is not None:
        known_folder_ids = {f.id for f in extract_folders(collection_data)}
        if folder_id not in known_folder_ids:
            raise validation_error(
                f"Unknown folder '{folder_id}'.",
                errors=[{"path": "$.folder_id", "detail": f"Unknown folder '{folder_id}'."}],
            )

    environment_values: dict[str, str] = {}
    if environment_raw is not None:
        parsed_env = parse_environment(
            environment_raw, filename=environment_filename or "environment.json", settings=settings,
        )
        environment_values = parsed_env.values

    collection_variables = collection_variable_values(collection_data)

    references = extract_variable_references(collection_data)
    variables = resolve_variables(
        references,
        collection_variables=collection_variables,
        environment_values=environment_values,
        supplied=supplied_values,
    )
    unresolved = unresolved_names(variables)
    if unresolved:
        raise validation_error(
            f"Cannot start execution: unresolved variables {', '.join(unresolved)}.",
            errors=[{"path": "$.supplied_values", "detail": f"Missing value for '{name}'."} for name in unresolved],
        )

    now = time.time()
    job = ExecutionJob(
        id=new_analysis_id(),
        owner_key=owner_key,
        collection_name=str(collection_data.get("info", {}).get("name") or "Untitled collection"),
        created_at=now,
        expires_at=now + settings.job_ttl_seconds,
        idempotency_key=idempotency_key,
        state=ExecutionJobState.QUEUED,
        warnings=list(parsed_collection.warnings),
    )
    store.create(job)
    return job, True


def prepare_run_material(
    *,
    collection_raw: bytes,
    collection_filename: str,
    environment_raw: bytes | None,
    environment_filename: str | None,
    supplied_values: dict[str, str],
    settings: Settings,
) -> tuple[dict, dict | None, dict[str, str]]:
    """Parse the collection/environment and merge variable values for `run_job`.

    Precedence (lowest to highest): collection variables < environment
    values < supplied values - the same precedence `create_job` validates
    against before ever persisting a job.

    This does real JSON parsing (and thus real work), so - exactly like
    `postman_inspector.inspect_collection` for `/inspect` - a caller in an
    async request handler must invoke this via `starlette.concurrency.
    run_in_threadpool` rather than calling it directly, to avoid blocking
    the event loop.
    """
    parsed_collection = parse_collection(collection_raw, filename=collection_filename, settings=settings)
    collection_data = parsed_collection.data
    collection_variables = collection_variable_values(collection_data)

    environment_data: dict | None = None
    environment_values: dict[str, str] = {}
    if environment_raw is not None:
        parsed_env = parse_environment(
            environment_raw, filename=environment_filename or "environment.json", settings=settings,
        )
        environment_data = parsed_env.data
        environment_values = parsed_env.values

    variable_values = {**collection_variables, **environment_values, **supplied_values}
    return collection_data, environment_data, variable_values


def _write_state(
    job_id: str,
    store: ExecutionJobStore,
    state: ExecutionJobState,
    *,
    error_code: str | None = None,
    error_detail: str | None = None,
    extra_warnings: list[str] | None = None,
    analysis_id: str | None = None,
) -> bool:
    """Atomically re-read and write `state` onto the job, honoring
    cancellation and never resurrecting a deleted job or overwriting a
    terminal state.

    The existence/terminal/cancel check and the write happen inside one
    `store.mutate` call, under the store's lock, so a concurrent delete or
    cancellation cannot land between the check and the write (`run_job` runs
    in a background threadpool, so this is a real race, not a hypothetical
    one).

    Returns True if `state` was written; False if the job is gone, already
    terminal, or cancellation preempted the write (in which case CANCELLED was
    written instead, unless the job was already terminal, in which case
    nothing was written at all).
    """
    written = False

    def _apply(fresh: ExecutionJob) -> None:
        nonlocal written
        if not fresh.is_active:
            return
        if fresh.cancel_requested:
            fresh.state = ExecutionJobState.CANCELLED
            fresh.stage_history.append(ExecutionJobState.CANCELLED.value)
            return
        fresh.state = state
        fresh.stage_history.append(state.value)
        if error_code is not None:
            fresh.error_code = error_code
        if error_detail is not None:
            fresh.error_detail = error_detail
        if extra_warnings:
            fresh.warnings.extend(extra_warnings)
        if analysis_id is not None:
            fresh.analysis_id = analysis_id
        written = True

    result = store.mutate(job_id, _apply)
    return result is not None and written


def _run_safely(runner: NewmanRunner, run_input: RunInput) -> RunOutcome | None:
    """Run the Newman runner, turning any exception into `None` so the caller
    never lets exception text (which may contain secrets) reach job fields.
    """
    try:
        return runner.run(run_input)
    except Exception:
        return None


def _outcome_error(outcome: RunOutcome, settings: Settings) -> tuple[str, str | None] | None:
    """Map a completed RunOutcome to a (error_code, error_detail) pair, or
    None if the outcome is a usable success.
    """
    if not outcome.success:
        return outcome.error_code or "run_failed", outcome.error_detail
    if not outcome.report_bytes:
        return "missing_report", "The Newman run did not produce a report."
    if len(outcome.report_bytes) > settings.max_report_bytes:
        return "report_too_large", "The Newman report exceeded the maximum allowed size."
    return None


def _run_stage(
    job_id: str,
    store: ExecutionJobStore,
    runner: NewmanRunner,
    run_input: RunInput,
    stage: ExecutionJobState,
    settings: Settings,
) -> RunOutcome | None:
    """Advance the job to `stage`, run the Newman runner once, and return a
    usable RunOutcome - or None if the stage could not proceed, having
    already written the appropriate terminal state (FAILED/CANCELLED) itself.

    Shared by both the baseline and comparison stages, which need identical
    runner-exception and outcome-validation handling.
    """
    if not _write_state(job_id, store, stage):
        return None
    outcome = _run_safely(runner, run_input)
    if outcome is None:
        _write_state(
            job_id, store, ExecutionJobState.FAILED,
            error_code="runner_error", error_detail="The Newman runner failed unexpectedly.",
        )
        return None
    error = _outcome_error(outcome, settings)
    if error is not None:
        code, detail = error
        _write_state(job_id, store, ExecutionJobState.FAILED, error_code=code, error_detail=detail)
        return None
    return outcome


def _redact_variable_values(text: str, variable_values: dict[str, str]) -> str:
    """Replace any supplied/resolved variable value of length >= 4 that
    appears in `text` with its `{{name}}` placeholder, longest values first so
    a value that is a substring of another value is not partially clobbered.

    Destination-validation warnings can otherwise echo a raw variable value
    (e.g. a blocked hostname supplied via a collection variable); per the
    project-wide rule that a job record never carries a supplied/resolved
    value, every warning must be passed through this before it reaches
    `job.warnings`.
    """
    redactable = sorted(
        ((name, value) for name, value in variable_values.items() if len(value) >= 4),
        key=lambda item: len(item[1]),
        reverse=True,
    )
    for name, value in redactable:
        text = text.replace(value, f"{{{{{name}}}}}")
    return text


def run_job(
    job_id: str,
    *,
    collection_data: dict,
    environment_data: dict | None,
    variable_values: dict[str, str],
    folder_id: str | None,
    runner: NewmanRunner,
    store: ExecutionJobStore,
    analysis_store: SessionStore,
    settings: Settings,
    resolver: destination_policy.Resolver | None = None,
) -> None:
    """Drive a queued execution job through its state machine.

    Every path ends in a terminal state (or a deliberate no-op when the job is
    missing, not queued, already terminal, or cancelled) so a job never gets
    stuck counting against the owner's concurrency cap - including when
    something inside the drive logic raises an exception we did not
    specifically anticipate (e.g. a resolver or the analysis store itself).
    """
    job = store.get(job_id)
    if job is None or job.state != ExecutionJobState.QUEUED:
        return

    try:
        _drive_job(
            job_id, collection_data=collection_data, environment_data=environment_data,
            variable_values=variable_values, folder_id=folder_id, runner=runner, store=store,
            analysis_store=analysis_store, settings=settings, resolver=resolver,
        )
    except Exception:
        _write_state(
            job_id, store, ExecutionJobState.FAILED,
            error_code="unexpected_error", error_detail="The execution job failed unexpectedly.",
        )


def _drive_job(
    job_id: str,
    *,
    collection_data: dict,
    environment_data: dict | None,
    variable_values: dict[str, str],
    folder_id: str | None,
    runner: NewmanRunner,
    store: ExecutionJobStore,
    analysis_store: SessionStore,
    settings: Settings,
    resolver: destination_policy.Resolver | None,
) -> None:
    if not _write_state(job_id, store, ExecutionJobState.VALIDATING):
        return

    domain_report = postman_domain_extractor.extract_target_domains(
        collection_data, environment_values=variable_values, settings=settings, resolver=resolver,
    )
    if domain_report.warnings:
        redacted_warnings = [_redact_variable_values(w, variable_values) for w in domain_report.warnings]
        _write_state(
            job_id, store, ExecutionJobState.FAILED,
            error_code="destination_validation_failed",
            error_detail="One or more request targets failed destination validation.",
            extra_warnings=redacted_warnings,
        )
        return

    run_input = RunInput(
        collection_data=collection_data, environment_data=environment_data,
        variable_values=variable_values, folder_id=folder_id,
        timeout_seconds=settings.job_run_timeout_seconds,
    )

    baseline = _run_stage(job_id, store, runner, run_input, ExecutionJobState.RUNNING_BASELINE, settings)
    if baseline is None:
        return

    comparison = _run_stage(job_id, store, runner, run_input, ExecutionJobState.RUNNING_COMPARISON, settings)
    if comparison is None:
        return

    if not _write_state(job_id, store, ExecutionJobState.ANALYZING):
        return

    # `_outcome_error` (inside `_run_stage`) already rejected a None/empty report for both outcomes.
    assert baseline.report_bytes is not None
    assert comparison.report_bytes is not None
    try:
        analysis = analysis_service.build_analysis(
            [("baseline.json", baseline.report_bytes), ("comparison.json", comparison.report_bytes)], settings,
        )
    except ProblemException as exc:
        _write_state(job_id, store, ExecutionJobState.FAILED, error_code=exc.code, error_detail=exc.detail)
        return
    except Exception:
        _write_state(
            job_id, store, ExecutionJobState.FAILED,
            error_code="analysis_error", error_detail="Analysis failed unexpectedly.",
        )
        return

    analysis_store.create(analysis)
    _write_state(job_id, store, ExecutionJobState.READY, analysis_id=analysis.id)
