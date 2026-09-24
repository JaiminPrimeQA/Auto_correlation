"""FastAPI application factory and middleware wiring."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from .api.v1 import analyses, downloads, execution_jobs, rules
from .core.config import get_settings
from .core.errors import (
    ProblemException,
    problem_exception_handler,
    unhandled_exception_handler,
)
from .core.logging import configure_logging, get_logger, new_request_id

log = get_logger("app")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        new_request_id()
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        return response


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()

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

    app.include_router(analyses.router, prefix=settings.api_prefix)
    app.include_router(rules.router, prefix=settings.api_prefix)
    app.include_router(downloads.router, prefix=settings.api_prefix)
    app.include_router(execution_jobs.router, prefix=settings.api_prefix)

    @app.get("/health", tags=["meta"])
    async def health() -> dict:
        return {"status": "ok", "app": settings.app_name, "version": "0.1.0"}

    return app


app = create_app()
