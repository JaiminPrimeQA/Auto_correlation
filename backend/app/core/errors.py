"""RFC 9457 problem-details error handling with stable machine-readable codes."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class ProblemException(Exception):
    """Application error rendered as an RFC 9457 problem+json document."""

    def __init__(
        self,
        *,
        status: int,
        code: str,
        title: str,
        detail: str | None = None,
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.errors = errors or []
        super().__init__(detail or title)

    def to_dict(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "type": f"about:blank#{self.code}",
            "title": self.title,
            "status": self.status,
            "code": self.code,
        }
        if self.detail:
            doc["detail"] = self.detail
        if self.errors:
            doc["errors"] = self.errors
        return doc


# --- Convenience constructors for common error codes ---


def validation_error(detail: str, errors: list[dict[str, Any]] | None = None) -> ProblemException:
    return ProblemException(
        status=422, code="validation_error", title="Validation failed",
        detail=detail, errors=errors,
    )


def not_found(detail: str) -> ProblemException:
    return ProblemException(status=404, code="not_found", title="Resource not found", detail=detail)


def payload_too_large(detail: str) -> ProblemException:
    return ProblemException(status=413, code="payload_too_large", title="Payload too large", detail=detail)


def rate_limited(detail: str) -> ProblemException:
    return ProblemException(status=429, code="rate_limited", title="Too many requests", detail=detail)


def conflict(detail: str) -> ProblemException:
    return ProblemException(status=409, code="conflict", title="Conflict", detail=detail)


def bad_request(code: str, detail: str) -> ProblemException:
    return ProblemException(status=400, code=code, title="Bad request", detail=detail)


async def problem_exception_handler(_: Request, exc: ProblemException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content=exc.to_dict(),
        media_type="application/problem+json",
    )


async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    # Never leak tracebacks in responses; details are logged server-side.
    return JSONResponse(
        status_code=500,
        content={
            "type": "about:blank#internal_error",
            "title": "Internal server error",
            "status": 500,
            "code": "internal_error",
        },
        media_type="application/problem+json",
    )
