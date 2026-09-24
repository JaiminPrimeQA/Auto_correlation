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
    collection_data: dict
    environment_data: dict | None
    variable_values: dict[str, str]
    folder_id: str | None
    timeout_seconds: int


@dataclass
class RunOutcome:
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
