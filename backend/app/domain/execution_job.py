"""Execution-job domain model: state machine, job record, and the typed
runner interface a Newman-executing worker implements.

`ExecutionJob` never carries a variable's resolved value or a Newman report's
bytes beyond the lifetime of one `run_job` call - those live only in local
variables during execution, never on this persisted record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

_TERMINAL = frozenset({"ready", "failed", "cancelled", "expired"})


class ExecutionJobState(str, Enum):
    UPLOADED = "uploaded"
    AWAITING_VARIABLES = "awaiting_variables"
    QUEUED = "queued"
    VALIDATING = "validating"
    RUNNING_BASELINE = "running_baseline"
    RUNNING_COMPARISON = "running_comparison"
    ANALYZING = "analyzing"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


@dataclass
class RunInput:
    """Everything one Newman run needs. `run_job` builds a fresh, deep-copied
    RunInput per run, so a runner may mutate it freely without affecting any
    other run.

    `supplied_values` holds ONLY the user-supplied runtime values. Newman
    resolves collection and environment variables natively from
    `collection_data` / `environment_data`; the merged collection <
    environment < supplied map is used only for destination validation and
    redaction and never reaches the runner.
    """

    collection_data: dict
    environment_data: dict | None
    supplied_values: dict[str, str]
    folder_id: str | None
    timeout_seconds: int


@dataclass
class RunOutcome:
    """The result of one Newman run.

    Contract: `error_detail` is copied verbatim onto the job record and shown
    to the client, so it MUST be a sanitized, user-safe message - never a
    secret, a supplied/resolved variable value, or raw Newman/process stderr.
    Runners map raw failures to a fixed message plus a stable `error_code`.
    """

    success: bool
    report_bytes: bytes | None = None
    error_code: str | None = None
    error_detail: str | None = None


class NewmanRunner(Protocol):
    def run(self, run_input: RunInput) -> RunOutcome: ...


@dataclass
class ExecutionJob:
    id: str
    owner_key: str
    collection_name: str
    created_at: float
    expires_at: float
    idempotency_key: str | None = None
    state: ExecutionJobState = ExecutionJobState.QUEUED
    analysis_id: str | None = None
    error_code: str | None = None
    error_detail: str | None = None
    stage_history: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    cancel_requested: bool = False

    @property
    def is_active(self) -> bool:
        return self.state.value not in _TERMINAL
