"""Ephemeral analysis state with TTL, behind a swappable store interface.

No database is required for the MVP. State lives only in an isolated session
that expires after a configurable TTL. The abstract ``SessionStore`` lets a
Redis-backed implementation replace the in-memory one for multi-worker use.
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..domain.enums import AnalysisMode
from ..domain.models import (
    AlignmentReport,
    ClassificationSummary,
    CorrelationCandidate,
    CorrelationRule,
    NormalizedRun,
    ReadinessReport,
    ValueInsight,
)


@dataclass
class Analysis:
    id: str
    mode: AnalysisMode
    created_at: float
    expires_at: float
    baseline_run: NormalizedRun
    comparison_run: NormalizedRun | None = None
    alignment: AlignmentReport | None = None
    candidates: list[CorrelationCandidate] = field(default_factory=list)
    insights: list[ValueInsight] = field(default_factory=list)
    summary: ClassificationSummary = field(default_factory=ClassificationSummary)
    readiness: ReadinessReport = field(default_factory=ReadinessReport)
    rules: dict[str, CorrelationRule] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    edited_count: int = 0
    deleted_count: int = 0
    generated_jmx: str | None = None
    generated_manifest: dict | None = None
    jmx_status: str | None = None       # JmxStatus value once generated/validated
    validation_report: dict | None = None  # JMeter execution report (P6)
    # One-click auto-correlate lifecycle (idempotency + UI button state).
    auto_correlation_status: str = "not_started"  # not_started|running|completed|failed
    auto_correlated_at: float | None = None
    auto_correlation_result: dict | None = None  # cached response for repeat calls
    # Who may read/modify this analysis (client IP, or `user:{sub}` with OIDC).
    owner_key: str | None = None

    @property
    def sequence_run(self) -> NormalizedRun:
        return self.baseline_run

    def existing_variable_names(self, *, exclude: str | None = None) -> set[str]:
        return {r.variable_name for rid, r in self.rules.items() if rid != exclude}

    def invalidate_generated(self) -> None:
        self.generated_jmx = None
        self.generated_manifest = None
        self.jmx_status = None
        self.validation_report = None


class SessionStore(ABC):
    @abstractmethod
    def create(self, analysis: Analysis) -> None: ...

    @abstractmethod
    def get(self, analysis_id: str) -> Analysis | None: ...

    @abstractmethod
    def delete(self, analysis_id: str) -> bool: ...

    @abstractmethod
    def cleanup(self) -> int: ...


class InMemorySessionStore(SessionStore):
    def __init__(self, ttl_seconds: int) -> None:
        self.ttl = ttl_seconds
        self._data: dict[str, Analysis] = {}
        self._lock = threading.RLock()

    def create(self, analysis: Analysis) -> None:
        with self._lock:
            self._data[analysis.id] = analysis

    def get(self, analysis_id: str) -> Analysis | None:
        with self._lock:
            self.cleanup()
            a = self._data.get(analysis_id)
            if a is None:
                return None
            if a.expires_at < time.time():
                self._data.pop(analysis_id, None)
                return None
            return a

    def delete(self, analysis_id: str) -> bool:
        with self._lock:
            return self._data.pop(analysis_id, None) is not None

    def cleanup(self) -> int:
        now = time.time()
        with self._lock:
            expired = [aid for aid, a in self._data.items() if a.expires_at < now]
            for aid in expired:
                self._data.pop(aid, None)
            return len(expired)
