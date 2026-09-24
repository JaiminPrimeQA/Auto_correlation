"""Shared FastAPI dependencies."""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, Request

from ..core.config import Settings, get_settings
from ..core.errors import not_found, rate_limited
from ..core.security import RateLimiter
from ..domain.execution_job import ExecutionJob, NewmanRunner
from ..repositories.analysis_store import Analysis, InMemorySessionStore, SessionStore
from ..repositories.execution_job_store import ExecutionJobStore, InMemoryExecutionJobStore
from ..services.fake_newman_runner import canned_fake_runner


@lru_cache
def get_store() -> SessionStore:
    settings = get_settings()
    return InMemorySessionStore(ttl_seconds=settings.session_ttl_seconds)


@lru_cache
def get_rate_limiter() -> RateLimiter:
    settings = get_settings()
    return RateLimiter(settings.rate_limit_requests, settings.rate_limit_window_seconds)


def get_owner_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def enforce_rate_limit(request: Request, settings: Settings = Depends(get_settings)) -> None:
    if not settings.rate_limit_enabled:
        return
    limiter = get_rate_limiter()
    if not limiter.allow(get_owner_key(request)):
        raise rate_limited("Too many requests; slow down.")


def require_analysis(analysis_id: str, store: SessionStore = Depends(get_store)) -> Analysis:
    analysis = store.get(analysis_id)
    if analysis is None:
        raise not_found("Analysis not found or expired.")
    return analysis


@lru_cache
def get_job_store() -> ExecutionJobStore:
    settings = get_settings()
    return InMemoryExecutionJobStore(ttl_seconds=settings.job_ttl_seconds)


def get_newman_runner(settings: Settings = Depends(get_settings)) -> NewmanRunner | None:
    """The runner that executes a newly created job, or None when execution is
    disabled (the default, spec §4) - the endpoint then answers 503 without
    creating a job. `fake` yields a fresh canned FakeNewmanRunner per request.
    """
    if settings.newman_runner == "fake":
        return canned_fake_runner()
    return None


def require_owned_job(
    job_id: str, request: Request, store: ExecutionJobStore = Depends(get_job_store),
) -> ExecutionJob:
    job = store.get(job_id)
    if job is None or job.owner_key != get_owner_key(request):
        raise not_found("Execution job not found or expired.")
    return job
