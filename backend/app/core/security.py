"""Security helpers: safe filenames, id generation, and rate limiting."""

from __future__ import annotations

import re
import secrets
import time
from collections import defaultdict, deque

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")


def safe_filename(raw: str | None, *, default: str = "upload.json") -> str:
    """Strip any path components and unsafe characters from a client filename."""
    if not raw:
        return default
    # Discard directory portions from either separator style.
    base = raw.replace("\\", "/").split("/")[-1]
    base = _SAFE_NAME.sub("_", base).strip("._") or default
    return base[:200]


def new_analysis_id() -> str:
    """Cryptographically random, unguessable analysis identifier."""
    return secrets.token_urlsafe(24)


class RateLimiter:
    """Simple in-process sliding-window limiter keyed by client identifier."""

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self.max_requests = max_requests
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self._hits[key]
        cutoff = now - self.window
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= self.max_requests:
            return False
        q.append(now)
        return True
