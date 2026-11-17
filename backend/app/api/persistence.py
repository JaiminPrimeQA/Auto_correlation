"""Save analyses a request changed before its response reaches the client.

Handlers mutate the ``Analysis`` object that ``require_analysis`` loaded.
This ASGI middleware waits for the response to start; on success it writes
every analysis the request touched back to the store, so the next request
(possibly on another replica) sees the change. On failure it drops the
cached copy instead, so a half-applied change is never served or saved.

A save that loses a race against another replica answers 409 ``conflict``
in place of the original response, and the client reloads.
"""

from __future__ import annotations

import json
from typing import Any

from starlette.concurrency import run_in_threadpool

from ..core.logging import get_logger
from ..repositories.analysis_store import Analysis, SessionStore
from ..repositories.persistent_analysis_store import AnalysisConflict

log = get_logger("persistence")

STATE_KEY = "b11_analyses"


def track(request: Any, analysis: Analysis, store: SessionStore) -> None:
    """Remember that this request loaded ``analysis`` from ``store``."""
    touched = getattr(request.state, STATE_KEY, None)
    if touched is None:
        touched = {}
        setattr(request.state, STATE_KEY, touched)
    touched[analysis.id] = (analysis, store)


def untrack(request: Any, analysis_id: str) -> None:
    """Forget an analysis the request deleted, so it is not saved back."""
    touched = getattr(request.state, STATE_KEY, None)
    if touched:
        touched.pop(analysis_id, None)


_CONFLICT = json.dumps({
    "type": "about:blank",
    "title": "Conflict",
    "status": 409,
    "code": "conflict",
    "detail": "This analysis was changed in another tab or by another server. Reload and try again.",
}).encode()


class AnalysisPersistenceMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        swallow = False

        async def send_wrapper(message: dict) -> None:
            nonlocal swallow
            if swallow:
                return
            if message["type"] == "http.response.start":
                touched: dict[str, tuple[Analysis, SessionStore]] = (scope.get("state") or {}).get(STATE_KEY) or {}
                if touched:
                    ok = await run_in_threadpool(
                        _finish, touched, int(message["status"]) < 400, scope.get("method", "GET") in _READS,
                    )
                    if not ok:
                        swallow = True
                        await send({
                            "type": "http.response.start",
                            "status": 409,
                            "headers": [(b"content-type", b"application/problem+json"),
                                        (b"content-length", str(len(_CONFLICT)).encode())],
                        })
                        await send({"type": "http.response.body", "body": _CONFLICT})
                        return
            await send(message)

        await self.app(scope, receive, send_wrapper)


_READS = frozenset({"GET", "HEAD", "OPTIONS"})


def _finish(touched: dict[str, tuple[Analysis, SessionStore]], success: bool, read_only: bool) -> bool:
    if success and read_only:
        return True
    if not success:
        for analysis_id, (_, store) in touched.items():
            store.discard(analysis_id)
        return True
    for analysis, store in touched.values():
        try:
            store.save(analysis)
        except AnalysisConflict:
            log.warning("analysis save conflict", extra={"stage": "persist", "analysis_id": analysis.id})
            return False
    return True
