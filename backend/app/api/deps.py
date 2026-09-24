"""Shared FastAPI dependencies."""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, Request

from ..core.config import Settings, get_settings
from ..core.errors import not_found, rate_limited
from ..core.security import RateLimiter
from ..repositories.analysis_store import Analysis, InMemorySessionStore, SessionStore


@lru_cache
def get_store() -> SessionStore:
    settings = get_settings()
    return InMemorySessionStore(ttl_seconds=settings.session_ttl_seconds)


@lru_cache
def get_rate_limiter() -> RateLimiter:
    settings = get_settings()
    return RateLimiter(settings.rate_limit_requests, settings.rate_limit_window_seconds)


def enforce_rate_limit(request: Request, settings: Settings = Depends(get_settings)) -> None:
    if not settings.rate_limit_enabled:
        return
    limiter = get_rate_limiter()
    client = request.client.host if request.client else "unknown"
    if not limiter.allow(client):
        raise rate_limited("Too many requests; slow down.")


def require_analysis(analysis_id: str, store: SessionStore = Depends(get_store)) -> Analysis:
    analysis = store.get(analysis_id)
    if analysis is None:
        raise not_found("Analysis not found or expired.")
    return analysis
