"""Shared FastAPI dependencies."""

from __future__ import annotations

import shutil
from functools import lru_cache

from fastapi import Depends, Request

from ..core.auth import AuthError, DiscoveringJwksClient, OidcVerifier, Principal
from ..core.config import Settings, get_settings
from ..core.errors import ProblemException, not_found, rate_limited, unauthorized
from ..core.security import RateLimiter
from ..domain.execution_job import ExecutionJob, NewmanRunner
from ..repositories.analysis_store import Analysis, InMemorySessionStore, SessionStore
from ..repositories.execution_job_store import ExecutionJobStore, InMemoryExecutionJobStore
from ..services.docker_newman_runner import DockerNewmanRunner
from ..services.fake_newman_runner import canned_fake_runner
from ..services.job_dispatcher import JobDispatcher, ThreadPoolJobDispatcher


@lru_cache
def get_store() -> SessionStore:
    settings = get_settings()
    return InMemorySessionStore(ttl_seconds=settings.session_ttl_seconds)


@lru_cache
def get_rate_limiter() -> RateLimiter:
    settings = get_settings()
    return RateLimiter(settings.rate_limit_requests, settings.rate_limit_window_seconds)


@lru_cache
def get_verifier() -> OidcVerifier | None:
    settings = get_settings()
    if settings.auth_mode != "oidc":
        return None
    if not settings.oidc_issuer or not settings.oidc_audience:
        raise RuntimeError("auth_mode=oidc requires B11_OIDC_ISSUER and B11_OIDC_AUDIENCE.")
    return OidcVerifier(
        issuer=settings.oidc_issuer,
        audience=settings.oidc_audience,
        jwks_client=DiscoveringJwksClient(settings.oidc_issuer, jwks_url=settings.oidc_jwks_url),
        algorithms=settings.oidc_algorithm_list,
    )


def _unauthorized(detail: str) -> ProblemException:
    exc = unauthorized(detail)
    exc.headers = {"WWW-Authenticate": 'Bearer realm="baseline11"'}
    return exc


def get_principal(
    request: Request,
    settings: Settings = Depends(get_settings),
    verifier: OidcVerifier | None = Depends(get_verifier),
) -> Principal | None:
    """The authenticated caller, or None when authentication is disabled.
    Applied to every /api/v1 router, so an unauthenticated request never
    reaches a handler in OIDC mode."""
    if settings.auth_mode != "oidc":
        return None
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthorized("Sign in to use this API.")
    if verifier is None:
        raise _unauthorized("Authentication is not configured.")
    try:
        return verifier.verify(token.strip())
    except AuthError as exc:
        raise _unauthorized(str(exc)) from exc


def get_owner_key(request: Request, principal: Principal | None = Depends(get_principal)) -> str:
    """Who owns what this request creates: the OIDC subject, or the client IP
    when authentication is disabled (local development)."""
    if principal is not None:
        return f"user:{principal.subject}"
    return request.client.host if request.client else "unknown"


def enforce_rate_limit(settings: Settings = Depends(get_settings), owner_key: str = Depends(get_owner_key)) -> None:
    if not settings.rate_limit_enabled:
        return
    limiter = get_rate_limiter()
    if not limiter.allow(owner_key):
        raise rate_limited("Too many requests; slow down.")


def require_analysis(
    analysis_id: str,
    store: SessionStore = Depends(get_store),
    owner_key: str = Depends(get_owner_key),
) -> Analysis:
    analysis = store.get(analysis_id)
    # Another owner's analysis is indistinguishable from a missing one.
    if analysis is None or analysis.owner_key != owner_key:
        raise not_found("Analysis not found or expired.")
    return analysis


@lru_cache
def get_job_store() -> ExecutionJobStore:
    settings = get_settings()
    return InMemoryExecutionJobStore(ttl_seconds=settings.job_ttl_seconds)


def get_newman_runner(settings: Settings = Depends(get_settings)) -> NewmanRunner | None:
    """The runner that executes a newly created job, or None when execution is
    unavailable (the default `disabled`, or `docker` without a Docker CLI) -
    the endpoint then answers 503 without creating a job (spec §4). `fake`
    yields a fresh canned FakeNewmanRunner per request.
    """
    if settings.newman_runner == "fake":
        return canned_fake_runner()
    if settings.newman_runner == "docker" and shutil.which(settings.docker_binary):
        return DockerNewmanRunner(settings)
    return None


@lru_cache
def get_job_dispatcher() -> JobDispatcher:
    return ThreadPoolJobDispatcher(max_workers=get_settings().max_concurrent_executions)


def require_owned_job(
    job_id: str,
    store: ExecutionJobStore = Depends(get_job_store),
    owner_key: str = Depends(get_owner_key),
) -> ExecutionJob:
    job = store.get(job_id)
    if job is None or job.owner_key != owner_key:
        raise not_found("Execution job not found or expired.")
    return job
