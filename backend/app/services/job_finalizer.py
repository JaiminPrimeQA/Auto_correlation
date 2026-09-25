"""AWS backend: turn the worker's two reports into an analysis in the API.

Analyses live in the API process (the existing product design), so the
worker stops at ANALYZING with both reports in S3 and the API finishes the
job the first time its owner polls it. A claim written atomically on the job
record makes exactly one request build the analysis; a claim older than
`_STALE_CLAIM_SECONDS` (an API task that died mid-way) can be taken over.
"""

from __future__ import annotations

import time

from ..core.config import Settings
from ..core.errors import ProblemException
from ..core.logging import get_logger
from ..domain.execution_job import ExecutionJob, ExecutionJobState
from ..repositories.analysis_store import SessionStore
from ..repositories.execution_job_store import ExecutionJobStore
from ..repositories.s3_object_store import ObjectTooLarge, S3ObjectStore, job_prefix, report_key
from . import analysis_service
from .execution_job_service import _log_swallowed, _write_state

log = get_logger("job_finalizer")

_STALE_CLAIM_SECONDS = 300


def needs_finalizing(job: ExecutionJob) -> bool:
    return job.state == ExecutionJobState.ANALYZING and job.reports_ready and job.analysis_id is None


def _fail(job_id: str, store: ExecutionJobStore, code: str, detail: str) -> None:
    _write_state(job_id, store, ExecutionJobState.FAILED, error_code=code, error_detail=detail)


def finalize_job(
    job_id: str,
    *,
    store: ExecutionJobStore,
    objects: S3ObjectStore,
    analysis_store: SessionStore,
    settings: Settings,
) -> None:
    claimed = False
    now = time.time()

    def _claim(fresh: ExecutionJob) -> None:
        nonlocal claimed
        stale = fresh.finalizing_since is None or now - fresh.finalizing_since > _STALE_CLAIM_SECONDS
        if needs_finalizing(fresh) and stale:
            fresh.finalizing_since = now
            claimed = True

    job = store.mutate(job_id, _claim)
    if job is None or not claimed:
        return

    try:
        reports: list[tuple[str, bytes]] = []
        for stage in ("baseline", "comparison"):
            data = objects.get_bytes(report_key(job_id, stage), max_bytes=settings.max_report_bytes)
            if data is None:
                _fail(job_id, store, "missing_report", "A Newman report was not found for analysis.")
                return
            reports.append((f"{stage}.json", data))
        analysis = analysis_service.build_analysis(reports, settings)
        analysis.owner_key = job.owner_key
        analysis_store.create(analysis)
        _write_state(job_id, store, ExecutionJobState.READY, analysis_id=analysis.id)
        log.info("execution job finalized", extra={"stage": "finalize", "job_id": job_id, "analysis_id": analysis.id})
    except ObjectTooLarge:
        _fail(job_id, store, "report_too_large", "The Newman report exceeded the maximum allowed size.")
    except ProblemException as exc:
        _fail(job_id, store, exc.code, exc.detail or "The Newman reports could not be analysed.")
    except Exception as exc:
        _log_swallowed(job_id, ExecutionJobState.ANALYZING.value, exc)
        _fail(job_id, store, "analysis_error", "Analysis failed unexpectedly.")
    finally:
        try:
            objects.delete_prefix(job_prefix(job_id))
        except Exception:  # noqa: BLE001 - the bucket lifecycle rule removes leftovers
            log.warning("could not delete job objects", extra={"stage": "finalize", "job_id": job_id})
