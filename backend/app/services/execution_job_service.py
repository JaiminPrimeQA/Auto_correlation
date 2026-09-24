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
from ..core.errors import rate_limited, validation_error
from ..core.security import new_analysis_id
from ..domain.execution_job import ExecutionJob, ExecutionJobState
from ..repositories.execution_job_store import ExecutionJobStore
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
