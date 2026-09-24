# backend/app/api/v1/execution_jobs.py
"""Execution-job endpoints for the Postman-collection input mode.

Only the pre-execution inspection endpoint exists so far. Job creation,
status, and cancellation (the asynchronous execution job model) are Phase 2
of the parent design and are not implemented here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from starlette.concurrency import run_in_threadpool

from ...core.config import Settings, get_settings
from ...core.logging import get_logger
from ...schemas import presenters
from ...services.postman_inspector import inspect_collection
from ..deps import enforce_rate_limit

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
