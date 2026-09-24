"""Structured logging that never emits bodies, tokens, or uploaded JSON."""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar

from ..utils.masking import redact_for_log

_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def new_request_id() -> str:
    rid = uuid.uuid4().hex[:12]
    _request_id.set(rid)
    return rid


def get_request_id() -> str:
    return _request_id.get()


class RedactingJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "request_id": get_request_id(),
            "msg": redact_for_log(record.getMessage()),
        }
        for key in ("stage", "count", "duration_ms", "error_code", "analysis_id", "job_id", "exc_type"):
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = val
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(RedactingJsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
