# backend/app/api/v1/execution_jobs.py
"""Execution-job endpoints for the Postman-collection input mode.

Covers the whole Phase 2 execution job lifecycle: pre-execution inspection,
job creation (which hands the run to the bounded job dispatcher), status
polling, and cancellation/cleanup.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, Header, UploadFile
from starlette.concurrency import run_in_threadpool

from ...core.config import Settings, get_settings
from ...core.errors import service_unavailable, validation_error
from ...core.logging import get_logger
from ...domain.execution_job import ExecutionJob, ExecutionJobState
from ...repositories.execution_job_store import ExecutionJobStore
from ...schemas import presenters
from ...services import execution_job_service
from ...services.job_launcher import JobLauncher, RunMaterial
from ...services.postman_inspector import inspect_collection
from ..deps import (
    enforce_rate_limit,
    get_job_launcher,
    get_job_store,
    get_owner_key,
    require_owned_job,
)

router = APIRouter(prefix="/execution-jobs", tags=["execution-jobs"])
log = get_logger("execution_jobs")


@router.post("/inspect")
async def inspect(
    collection: UploadFile = File(...),
    environment: UploadFile | None = File(None),
    settings: Settings = Depends(get_settings),
    _: None = Depends(enforce_rate_limit),
) -> dict:
    collection_raw = await collection.read()
    environment_raw = await environment.read() if environment is not None else None
    environment_filename = environment.filename if environment is not None else None

    inspection = await run_in_threadpool(
        inspect_collection,
        collection_raw=collection_raw,
        collection_filename=collection.filename or "collection.json",
        environment_raw=environment_raw,
        environment_filename=environment_filename,
        settings=settings,
    )
    log.info(
        "collection inspected",
        extra={
            "stage": "inspect",
            "folder_count": len(inspection.folders),
            "variable_count": len(inspection.variables),
            "unresolved_count": len(inspection.unresolved_variable_names),
            "unsupported_count": len(inspection.unsupported_features),
        },
    )
    return presenters.postman_inspection_dto(inspection)


def _reject_non_finite(literal: str) -> float:
    """`json.loads` hook for the non-standard NaN/Infinity/-Infinity literals
    Python otherwise accepts: they are not valid JSON and would canonicalize
    to "NaN"/"Infinity", so reject them outright.
    """
    raise validation_error(
        f"'supplied_values_json' must not contain {literal}.",
        errors=[{"path": "$.supplied_values", "detail": "NaN and Infinity are not allowed."}],
    )


def _coerce_supplied_values(raw: dict) -> dict[str, str]:
    """Canonicalize JSON-decoded `supplied_values_json` entries to strings.

    Only JSON strings, numbers, and booleans are accepted - `null`, arrays,
    and objects are rejected outright rather than silently stringified
    (`str(None)` -> "None", `str([1])` -> "[1]", `str({"a": 1})` ->
    "{'a': 1}" would otherwise leak Python repr syntax into a variable
    substitution). Numbers and booleans are canonicalized via `json.dumps`
    so they read the way they would inline in JSON/a Postman request
    (`True` -> "true", `1` -> "1", `1.5` -> "1.5"); strings pass through
    unchanged.
    """
    invalid_keys = sorted(str(k) for k, v in raw.items() if not isinstance(v, (str, bool, int, float)))
    if invalid_keys:
        raise validation_error(
            f"Supplied values must be strings, numbers, or booleans: invalid key(s) {', '.join(invalid_keys)}.",
            errors=[
                {"path": f"$.supplied_values.{k}", "detail": "Value must be a string, number, or boolean."}
                for k in invalid_keys
            ],
        )
    return {str(k): v if isinstance(v, str) else json.dumps(v) for k, v in raw.items()}


@router.post("", status_code=202)
async def create_execution_job(
    collection: UploadFile = File(...),
    environment: UploadFile | None = File(None),
    folder_id: str | None = Form(None),
    supplied_values_json: str = Form("{}"),
    confirm: bool = Form(...),
    settings: Settings = Depends(get_settings),
    job_store: ExecutionJobStore = Depends(get_job_store),
    owner_key: str = Depends(get_owner_key),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    launcher: JobLauncher | None = Depends(get_job_launcher),
    _: None = Depends(enforce_rate_limit),
) -> dict:
    # Spec §4: with no runner configured (the default) nothing may execute, so
    # refuse before any job state exists rather than queue a job that can
    # never run.
    if launcher is None:
        raise service_unavailable("runner_unavailable", "Collection execution is not enabled on this server.")
    if not confirm:
        raise validation_error("Execution requires explicit confirmation (confirm=true).")
    try:
        supplied_values = json.loads(supplied_values_json, parse_constant=_reject_non_finite)
    except json.JSONDecodeError as exc:
        raise validation_error("'supplied_values_json' is not valid JSON.") from exc
    if not isinstance(supplied_values, dict):
        raise validation_error("'supplied_values_json' must be a JSON object.")
    supplied_values = _coerce_supplied_values(supplied_values)

    collection_raw = await collection.read()
    environment_raw = await environment.read() if environment is not None else None
    environment_filename = environment.filename if environment is not None else None
    collection_filename = collection.filename or "collection.json"

    # Parse the run material first: a problem here must never leave a job
    # persisted that nothing will ever run.
    collection_data, environment_data, variable_values = await run_in_threadpool(
        execution_job_service.prepare_run_material,
        collection_raw=collection_raw,
        collection_filename=collection_filename,
        environment_raw=environment_raw,
        environment_filename=environment_filename,
        supplied_values=supplied_values,
        settings=settings,
    )

    job, created = await run_in_threadpool(
        execution_job_service.create_job,
        collection_raw=collection_raw,
        collection_filename=collection_filename,
        environment_raw=environment_raw,
        environment_filename=environment_filename,
        folder_id=folder_id,
        supplied_values=supplied_values,
        owner_key=owner_key,
        idempotency_key=idempotency_key,
        store=job_store,
        settings=settings,
    )
    # Built before dispatching: an inline dispatcher would otherwise run the
    # job to completion first and the response would not show the created state.
    dto = presenters.execution_job_dto(job)
    dto["status_url"] = f"{settings.api_prefix}/execution-jobs/{job.id}"

    # `created` is False on an idempotency-key hit that returns an existing
    # job (possibly already past QUEUED, or even terminal) - launching then
    # would start a second run of the same job.
    if created:
        material = RunMaterial(
            collection_data=collection_data,
            environment_data=environment_data,
            variable_values=variable_values,
            supplied_values=supplied_values,
            folder_id=folder_id,
        )
        await run_in_threadpool(launcher.launch, job.id, material)

    log.info(
        "execution job created",
        extra={"stage": "create_job", "job_id": job.id, "state": dto["state"], "was_created": created},
    )
    return dto


@router.get("/{job_id}")
async def get_execution_job(job: ExecutionJob = Depends(require_owned_job)) -> dict:
    return presenters.execution_job_dto(job)


@router.delete("/{job_id}", status_code=204)
async def delete_execution_job(
    job: ExecutionJob = Depends(require_owned_job),
    job_store: ExecutionJobStore = Depends(get_job_store),
) -> None:
    cancelled = False

    def _cancel_if_active(fresh: ExecutionJob) -> None:
        nonlocal cancelled
        if fresh.is_active:
            fresh.cancel_requested = True
            fresh.state = ExecutionJobState.CANCELLED
            fresh.stage_history.append(ExecutionJobState.CANCELLED.value)
            cancelled = True

    # Atomic check-and-write under the store's lock (A8): a plain
    # read-modify-`update` here could race with a concurrent `run_job`
    # write (lost update) or a concurrent delete (resurrecting a deleted
    # job). `mutate` returns None if the job is already gone/expired, in
    # which case there is nothing left to cancel or delete.
    result = job_store.mutate(job.id, _cancel_if_active)
    if result is not None and not cancelled and not result.worker_active:
        # The job was already terminal (or became terminal before the
        # mutate ran) - nothing to cancel, so remove it outright. A terminal
        # job whose worker is still live is kept (cancelled) until the worker
        # finishes: removing it would free the owner's concurrency slot while
        # the worker still runs. A later DELETE, or TTL cleanup, removes it.
        job_store.delete(job.id)
