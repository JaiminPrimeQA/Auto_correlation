"""FastAPI application factory and middleware wiring."""

from __future__ import annotations

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware

from .api.deps import get_principal
from .api.v1 import analyses, downloads, execution_jobs, rules
from .core.config import Settings, get_settings
from .core.errors import (
    ProblemException,
    problem_exception_handler,
    unhandled_exception_handler,
)
from .core.logging import configure_logging, get_logger, new_request_id
from .core.production_guard import check_production_settings

log = get_logger("app")


def dependency_checks(settings: Settings) -> dict[str, bool]:
    """Reachability of what the API needs; nothing to check locally."""
    if settings.job_backend != "aws":
        return {}
    from .api import deps

    checks: dict[str, bool] = {}
    probes = {
        "job_store": lambda: deps.get_job_store().get("readiness-probe"),
        "queue": lambda: deps.get_job_queue().ping(),
        "bucket": lambda: deps.get_object_store().get_bytes("readiness-probe", max_bytes=1),
    }
    for name, probe in probes.items():
        try:
            probe()
            checks[name] = True
        except Exception:  # noqa: BLE001 - reported as not ready, details stay in logs
            log.warning(f"readiness check failed: {name}", extra={"stage": "ready"})
            checks[name] = False
    return checks


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = new_request_id(request.headers.get("X-Request-ID"))
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        return response


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()
    check_production_settings(settings, role="api")

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Transform Newman JSON reports into auto-correlated JMeter test plans.",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["*"],
    )

    app.add_exception_handler(ProblemException, problem_exception_handler)  # type: ignore[arg-type]
    if settings.is_production:
        app.add_exception_handler(Exception, unhandled_exception_handler)

    # Every API route authenticates first (a no-op when auth_mode=disabled).
    authenticated = [Depends(get_principal)]
    app.include_router(analyses.router, prefix=settings.api_prefix, dependencies=authenticated)
    app.include_router(rules.router, prefix=settings.api_prefix, dependencies=authenticated)
    app.include_router(downloads.router, prefix=settings.api_prefix, dependencies=authenticated)
    app.include_router(execution_jobs.router, prefix=settings.api_prefix, dependencies=authenticated)

    @app.get("/health", tags=["meta"])
    async def health() -> dict:
        return {"status": "ok", "app": settings.app_name, "version": "0.1.0"}

    @app.get("/health/ready", tags=["meta"])
    async def ready() -> JSONResponse:
        checks = await run_in_threadpool(dependency_checks, get_settings())
        ok = all(checks.values())
        return JSONResponse(
            status_code=200 if ok else 503,
            content={"status": "ready" if ok else "not_ready", "checks": checks},
        )

    return app


app = create_app()
