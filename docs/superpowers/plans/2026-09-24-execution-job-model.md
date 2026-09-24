# Execution Job Model (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the asynchronous execution-job model — state machine, persistence, a typed `NewmanRunner` interface with a fake test-double implementation, and the `POST/GET/DELETE /api/v1/execution-jobs` endpoints — so a tester can submit resolved Postman-collection inputs, get a job id back immediately, poll its progress, and land on the existing analysis pipeline once it completes. No real Newman process runs in this phase; a `FakeNewmanRunner` drives the state machine so the whole job lifecycle, API surface, and correlation hand-off are provably correct before Phase 3 wires in the real Docker runner.

**Architecture:** A job domain model (`ExecutionJob`, `ExecutionJobState`) behind a TTL-expiring, owner/idempotency-indexed store (mirroring the existing `SessionStore`/`InMemorySessionStore` pattern exactly). A `create_job` service validates and resolves inputs synchronously (reusing Phase 1's `postman_inspector`/`postman_variable_resolver` unchanged) and persists a `queued` job; a `run_job` service — dispatched via FastAPI `BackgroundTasks` so the HTTP request returns immediately — drives the job through `validating → running_baseline → running_comparison → analyzing → ready|failed` using an injected `NewmanRunner`, then calls the existing `analysis_service.build_analysis` unchanged to produce an `analysis_id`.

**Tech Stack:** FastAPI `BackgroundTasks` (part of the framework, no new dependency), Pydantic v2, Python stdlib only.

**Spec:** [docs/specs/2026-09-24-postman-collection-execution-design.md](../../specs/2026-09-24-postman-collection-execution-design.md) — this plan implements design §5.1 items 4 and 6 (Execution job service, Correlation adapter) and the job-lifecycle half of §6 (`POST/GET/DELETE /api/v1/execution-jobs`). §5.1 item 5 (Newman runner interface) is implemented here only as a *typed interface* plus a fake test double — the real Docker/ECS implementations are Phase 3.

## Global Constraints

- States (exact names, from spec §5.1.4): `uploaded`, `awaiting_variables`, `queued`, `validating`, `running_baseline`, `running_comparison`, `analyzing`, `ready`, `failed`, `cancelled`, `expired`.
- Exactly two runs per job (baseline, comparison) — no partial-run analysis in this flow.
- Unresolved variables must block job creation with a specific 422, never fall through as empty strings.
- Never construct a shell command string; the `NewmanRunner` interface takes a typed `RunInput`, never raw argv/shell text.
- Never keep the original `POST /execution-jobs` request open while the two runs execute — creation returns `202 Accepted` immediately; execution happens via a `BackgroundTasks` callback.
- Never log or persist a supplied/resolved variable *value* beyond the lifetime of one job's execution; the report bytes and merged variable values are held only in memory during `run_job` and are never written to the job's own persisted/returned fields.
- **Design decision (documented, not a deviation to silently make later):** the app has no authentication system yet (Phase 5 owns "authenticated ownership integration" per the parent design). This phase keys job ownership by `owner_key = request.client.host if request.client else "unknown"` — the same client identifier `RateLimiter` already uses (`app/api/deps.py`) — as a placeholder. `ExecutionJob.owner_key` is a plain string field so swapping in a real user id later is a one-line change at the call site, not a schema change.
- **Design decision:** `awaiting_variables` is defined in `ExecutionJobState` for lifecycle completeness and parity with the spec's state list, but this phase's single-shot `POST /execution-jobs` (which requires all values supplied up front) never lands a job there — if variables remain unresolved after applying `supplied`, job creation is rejected with a 422 before any `ExecutionJob` is persisted. A future phase that adds a way to resupply values to an existing job would be the first to actually reach this state.
- Concurrency: `Settings.max_concurrent_jobs_per_owner` default 2 (design's "two active jobs per user by default"); "active" means any non-terminal state (`ready`/`failed`/`cancelled`/`expired` are terminal).
- Idempotency: an optional `Idempotency-Key` request header, scoped per `owner_key`; a repeat submission with the same key while the prior job is still active returns that same job instead of creating a new one.
- Follow existing conventions exactly: `Settings` from `app/core/config.py`, RFC 9457 `ProblemException` errors, the `SessionStore`/`InMemorySessionStore` dataclass-plus-ABC-plus-threading.RLock pattern in `app/repositories/analysis_store.py`, `from __future__ import annotations` in every new module, ruff line-length 120.
- TDD: write the failing test first, watch it fail for the stated reason, then implement.

---

### Task 1: Domain model — job state, `ExecutionJob`, and the `NewmanRunner` interface

**Files:**
- Create: `backend/app/domain/execution_job.py`
- Test: `backend/tests/unit/test_execution_job_model.py`

**Interfaces:**
- Produces: `ExecutionJobState(str, Enum)` with the 11 exact spec states; `RunInput(collection_data: dict, environment_data: dict | None, variable_values: dict[str, str], folder_id: str | None, timeout_seconds: int)`; `RunOutcome(success: bool, report_bytes: bytes | None, error_code: str | None, error_detail: str | None)`; `NewmanRunner` (`typing.Protocol`) with `def run(self, run_input: RunInput) -> RunOutcome`; `ExecutionJob` dataclass. Every later task in this plan imports these exact names.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_execution_job_model.py
import time

from app.domain.execution_job import ExecutionJob, ExecutionJobState, RunInput, RunOutcome


def test_execution_job_state_has_exact_spec_states():
    names = {s.value for s in ExecutionJobState}
    assert names == {
        "uploaded", "awaiting_variables", "queued", "validating",
        "running_baseline", "running_comparison", "analyzing",
        "ready", "failed", "cancelled", "expired",
    }


def test_execution_job_defaults():
    job = ExecutionJob(
        id="job_1", owner_key="127.0.0.1", collection_name="Demo",
        created_at=time.time(), expires_at=time.time() + 60,
    )
    assert job.state == ExecutionJobState.QUEUED
    assert job.idempotency_key is None
    assert job.analysis_id is None
    assert job.error_code is None
    assert job.error_detail is None
    assert job.stage_history == []
    assert job.cancel_requested is False


def test_execution_job_is_active_for_non_terminal_states_only():
    job = ExecutionJob(id="j", owner_key="x", collection_name="d", created_at=0, expires_at=0)
    assert job.is_active is True
    for terminal in (ExecutionJobState.READY, ExecutionJobState.FAILED,
                     ExecutionJobState.CANCELLED, ExecutionJobState.EXPIRED):
        job.state = terminal
        assert job.is_active is False


def test_run_input_and_run_outcome_construct():
    RunInput(collection_data={"info": {"name": "x"}, "item": []}, environment_data=None,
              variable_values={"host": "api.example.com"}, folder_id=None, timeout_seconds=300)
    RunOutcome(success=True, report_bytes=b'{"run": {}}', error_code=None, error_detail=None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_execution_job_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.domain.execution_job'`

- [ ] **Step 3: Implement the domain model**

```python
# backend/app/domain/execution_job.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_execution_job_model.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/domain/execution_job.py backend/tests/unit/test_execution_job_model.py
git commit -m "feat(execution-job): add job state machine and NewmanRunner interface"
```

---

### Task 2: `FakeNewmanRunner` test double

**Files:**
- Create: `backend/app/services/fake_newman_runner.py`
- Test: `backend/tests/unit/test_fake_newman_runner.py`

**Interfaces:**
- Consumes: `NewmanRunner`, `RunInput`, `RunOutcome` (Task 1)
- Produces: `FakeNewmanRunner(outcomes: list[RunOutcome])` implementing `NewmanRunner`, consuming one `RunOutcome` per `.run()` call in order, plus `FakeNewmanRunner.calls: list[RunInput]` recording every call for test assertions. Task 6 (`run_job`) and Task 12 (integration tests) use this as their injected runner.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_fake_newman_runner.py
import pytest

from app.domain.execution_job import RunInput, RunOutcome
from app.services.fake_newman_runner import FakeNewmanRunner


def _run_input(**overrides) -> RunInput:
    base = dict(collection_data={"info": {"name": "x"}, "item": []}, environment_data=None,
                variable_values={}, folder_id=None, timeout_seconds=300)
    base.update(overrides)
    return RunInput(**base)


def test_returns_outcomes_in_order():
    outcomes = [RunOutcome(success=True, report_bytes=b"1"), RunOutcome(success=True, report_bytes=b"2")]
    runner = FakeNewmanRunner(outcomes)
    assert runner.run(_run_input()) is outcomes[0]
    assert runner.run(_run_input()) is outcomes[1]


def test_records_every_call():
    runner = FakeNewmanRunner([RunOutcome(success=True), RunOutcome(success=True)])
    a, b = _run_input(folder_id="auth"), _run_input(variable_values={"host": "x"})
    runner.run(a)
    runner.run(b)
    assert runner.calls == [a, b]


def test_raises_if_called_more_times_than_outcomes_configured():
    runner = FakeNewmanRunner([RunOutcome(success=True)])
    runner.run(_run_input())
    with pytest.raises(IndexError):
        runner.run(_run_input())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_fake_newman_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.fake_newman_runner'`

- [ ] **Step 3: Implement the fake runner**

```python
# backend/app/services/fake_newman_runner.py
"""A deterministic NewmanRunner test double.

Used by Task 6's `run_job` tests and by the Task 12 integration tests to
drive the full job lifecycle without a real Newman process or Docker.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.execution_job import NewmanRunner, RunInput, RunOutcome


@dataclass
class FakeNewmanRunner(NewmanRunner):
    outcomes: list[RunOutcome]
    calls: list[RunInput] = field(default_factory=list)

    def run(self, run_input: RunInput) -> RunOutcome:
        self.calls.append(run_input)
        return self.outcomes.pop(0)
```

Note: `.pop(0)` on an empty list raises `IndexError`, matching the test's expectation directly — no extra bookkeeping needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_fake_newman_runner.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/fake_newman_runner.py backend/tests/unit/test_fake_newman_runner.py
git commit -m "feat(execution-job): add FakeNewmanRunner test double"
```

---

### Task 3: Config additions for job limits

**Files:**
- Modify: `backend/app/core/config.py`
- Test: `backend/tests/unit/test_config_execution_job.py`

**Interfaces:**
- Produces: `Settings.max_concurrent_jobs_per_owner: int`, `Settings.job_ttl_seconds: int`, `Settings.job_run_timeout_seconds: int`, `Settings.max_report_bytes: int`, `Settings.idempotency_window_seconds: int`. Tasks 4-8 read these.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_config_execution_job.py
from app.core.config import Settings


def test_execution_job_limit_defaults():
    s = Settings()
    assert s.max_concurrent_jobs_per_owner == 2
    assert s.job_ttl_seconds == 30 * 60
    assert s.job_run_timeout_seconds == 5 * 60
    assert s.max_report_bytes == 25 * 1024 * 1024
    assert s.idempotency_window_seconds == 10 * 60
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_config_execution_job.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'max_concurrent_jobs_per_owner'`

- [ ] **Step 3: Add the settings**

Add to `backend/app/core/config.py`, below the `# --- Public-HTTPS destination policy ---` block:

```python
    # --- Execution jobs (Phase 2: state model + fake runner) ---
    max_concurrent_jobs_per_owner: int = 2
    job_ttl_seconds: int = 30 * 60
    job_run_timeout_seconds: int = 5 * 60
    max_report_bytes: int = 25 * MIB
    # A repeat POST with the same Idempotency-Key header, from the same owner,
    # within this window returns the existing job instead of starting a new one.
    idempotency_window_seconds: int = 10 * 60
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_config_execution_job.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/config.py backend/tests/unit/test_config_execution_job.py
git commit -m "feat(config): add execution-job limits"
```

---

### Task 4: `ExecutionJobStore` — persistence, TTL expiry, ownership, idempotency, concurrency

**Files:**
- Create: `backend/app/repositories/execution_job_store.py`
- Test: `backend/tests/unit/test_execution_job_store.py`

**Interfaces:**
- Consumes: `ExecutionJob`, `ExecutionJobState` (Task 1); `Settings.job_ttl_seconds`, `Settings.max_concurrent_jobs_per_owner`, `Settings.idempotency_window_seconds` (Task 3)
- Produces: `ExecutionJobStore` (ABC) with `create`, `get(job_id) -> ExecutionJob | None`, `update(job: ExecutionJob) -> None`, `delete(job_id) -> bool`, `find_by_idempotency_key(owner_key, idempotency_key) -> ExecutionJob | None`, `count_active(owner_key) -> int`; `InMemoryExecutionJobStore` implementing it. Task 5/6/7/8/9 all depend on this exact interface.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_execution_job_store.py
import time

import pytest

from app.domain.execution_job import ExecutionJob, ExecutionJobState
from app.repositories.execution_job_store import InMemoryExecutionJobStore


def _job(id="j1", owner="127.0.0.1", ttl=60, **overrides) -> ExecutionJob:
    base = dict(id=id, owner_key=owner, collection_name="Demo",
                created_at=time.time(), expires_at=time.time() + ttl)
    base.update(overrides)
    return ExecutionJob(**base)


@pytest.fixture
def store():
    return InMemoryExecutionJobStore(ttl_seconds=60)


def test_create_and_get(store):
    job = _job()
    store.create(job)
    assert store.get("j1") is job


def test_get_missing_returns_none(store):
    assert store.get("missing") is None


def test_get_expired_returns_none_and_evicts(store):
    store.create(_job(ttl=-1))
    assert store.get("j1") is None
    assert store.get("j1") is None  # already evicted, still None


def test_update_persists_mutation(store):
    job = _job()
    store.create(job)
    job.state = ExecutionJobState.READY
    store.update(job)
    assert store.get("j1").state == ExecutionJobState.READY


def test_delete(store):
    store.create(_job())
    assert store.delete("j1") is True
    assert store.get("j1") is None
    assert store.delete("j1") is False


def test_find_by_idempotency_key_scoped_to_owner(store):
    store.create(_job(id="a", owner="owner1", idempotency_key="k1"))
    store.create(_job(id="b", owner="owner2", idempotency_key="k1"))
    found = store.find_by_idempotency_key("owner1", "k1")
    assert found.id == "a"
    assert store.find_by_idempotency_key("owner2", "k1").id == "b"
    assert store.find_by_idempotency_key("owner1", "no-such-key") is None


def test_find_by_idempotency_key_ignores_terminal_jobs(store):
    store.create(_job(id="a", owner="o", idempotency_key="k", state=ExecutionJobState.FAILED))
    assert store.find_by_idempotency_key("o", "k") is None


def test_count_active_excludes_terminal_states(store):
    store.create(_job(id="a", owner="o", state=ExecutionJobState.QUEUED))
    store.create(_job(id="b", owner="o", state=ExecutionJobState.RUNNING_BASELINE))
    store.create(_job(id="c", owner="o", state=ExecutionJobState.READY))
    store.create(_job(id="d", owner="other", state=ExecutionJobState.QUEUED))
    assert store.count_active("o") == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_execution_job_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.repositories.execution_job_store'`

- [ ] **Step 3: Implement the store**

```python
# backend/app/repositories/execution_job_store.py
"""Ephemeral execution-job state with TTL, mirroring `analysis_store.py`'s
SessionStore pattern exactly. Adds an owner+idempotency-key index and an
active-job counter that Task 5's `create_job` uses to enforce concurrency
and idempotent resubmission.
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod

from ..domain.execution_job import ExecutionJob


class ExecutionJobStore(ABC):
    @abstractmethod
    def create(self, job: ExecutionJob) -> None: ...

    @abstractmethod
    def get(self, job_id: str) -> ExecutionJob | None: ...

    @abstractmethod
    def update(self, job: ExecutionJob) -> None: ...

    @abstractmethod
    def delete(self, job_id: str) -> bool: ...

    @abstractmethod
    def find_by_idempotency_key(self, owner_key: str, idempotency_key: str) -> ExecutionJob | None: ...

    @abstractmethod
    def count_active(self, owner_key: str) -> int: ...


class InMemoryExecutionJobStore(ExecutionJobStore):
    def __init__(self, ttl_seconds: int) -> None:
        self.ttl = ttl_seconds
        self._data: dict[str, ExecutionJob] = {}
        self._lock = threading.RLock()

    def create(self, job: ExecutionJob) -> None:
        with self._lock:
            self._data[job.id] = job

    def get(self, job_id: str) -> ExecutionJob | None:
        with self._lock:
            self._cleanup()
            job = self._data.get(job_id)
            if job is None:
                return None
            if job.expires_at < time.time():
                self._data.pop(job_id, None)
                return None
            return job

    def update(self, job: ExecutionJob) -> None:
        with self._lock:
            self._data[job.id] = job

    def delete(self, job_id: str) -> bool:
        with self._lock:
            return self._data.pop(job_id, None) is not None

    def find_by_idempotency_key(self, owner_key: str, idempotency_key: str) -> ExecutionJob | None:
        with self._lock:
            self._cleanup()
            for job in self._data.values():
                if job.owner_key == owner_key and job.idempotency_key == idempotency_key and job.is_active:
                    return job
            return None

    def count_active(self, owner_key: str) -> int:
        with self._lock:
            self._cleanup()
            return sum(1 for j in self._data.values() if j.owner_key == owner_key and j.is_active)

    def _cleanup(self) -> None:
        now = time.time()
        expired = [jid for jid, j in self._data.items() if j.expires_at < now]
        for jid in expired:
            self._data.pop(jid, None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_execution_job_store.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/execution_job_store.py backend/tests/unit/test_execution_job_store.py
git commit -m "feat(execution-job): add ExecutionJobStore with TTL, ownership, and idempotency indexing"
```

---

### Task 5: `create_job` — validate, resolve, gate, and persist a queued job

**Files:**
- Create: `backend/app/services/execution_job_service.py`
- Test: `backend/tests/unit/test_execution_job_service_create.py`

**Interfaces:**
- Consumes: `parse_collection`, `parse_environment` (Phase 1 `postman_parser.py`); `extract_variable_references` (Phase 1 `postman_variable_extractor.py`); `resolve_variables`, `unresolved_names` (Phase 1 `postman_variable_resolver.py`); `ExecutionJob`, `ExecutionJobState` (Task 1); `ExecutionJobStore` (Task 4); `Settings.max_concurrent_jobs_per_owner`, `Settings.job_ttl_seconds` (Task 3); `new_analysis_id` (existing `app/core/security.py`, reused as a generic opaque-id generator); `rate_limited`, `validation_error` (existing `app/core/errors.py`)
- Produces: `create_job(*, collection_raw, collection_filename, environment_raw, environment_filename, folder_id, supplied_values, owner_key, idempotency_key, store, settings) -> ExecutionJob`. Task 10 (API endpoint) calls this.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_execution_job_service_create.py
import json

import pytest

from app.core.config import Settings
from app.core.errors import ProblemException
from app.domain.execution_job import ExecutionJobState
from app.repositories.execution_job_store import InMemoryExecutionJobStore
from app.services.execution_job_service import create_job
from tests.fixtures.postman_builders import pm_collection, pm_environment, pm_request


def _collection():
    return pm_collection("Login Flow", [pm_request("Login", "POST", "https://{{host}}/login")])


def _store():
    return InMemoryExecutionJobStore(ttl_seconds=60)


def test_creates_queued_job_when_all_variables_resolved():
    raw = json.dumps(_collection()).encode()
    store = _store()
    job = create_job(
        collection_raw=raw, collection_filename="c.json",
        environment_raw=None, environment_filename=None,
        folder_id=None, supplied_values={"host": "93.184.216.34"},
        owner_key="127.0.0.1", idempotency_key=None,
        store=store, settings=Settings(),
    )
    assert job.state == ExecutionJobState.QUEUED
    assert job.collection_name == "Login Flow"
    assert store.get(job.id) is job


def test_rejects_when_variables_remain_unresolved():
    raw = json.dumps(_collection()).encode()
    with pytest.raises(ProblemException) as exc:
        create_job(
            collection_raw=raw, collection_filename="c.json",
            environment_raw=None, environment_filename=None,
            folder_id=None, supplied_values={},
            owner_key="127.0.0.1", idempotency_key=None,
            store=_store(), settings=Settings(),
        )
    assert exc.value.status == 422
    assert "host" in exc.value.detail


def test_environment_values_satisfy_resolution_without_supplied():
    raw = json.dumps(_collection()).encode()
    env = json.dumps(pm_environment("dev", {"host": "93.184.216.34"})).encode()
    job = create_job(
        collection_raw=raw, collection_filename="c.json",
        environment_raw=env, environment_filename="e.json",
        folder_id=None, supplied_values={},
        owner_key="127.0.0.1", idempotency_key=None,
        store=_store(), settings=Settings(),
    )
    assert job.state == ExecutionJobState.QUEUED


def test_supplied_values_never_appear_on_the_returned_job():
    raw = json.dumps(_collection()).encode()
    job = create_job(
        collection_raw=raw, collection_filename="c.json",
        environment_raw=None, environment_filename=None,
        folder_id=None, supplied_values={"host": "93.184.216.34"},
        owner_key="127.0.0.1", idempotency_key=None,
        store=_store(), settings=Settings(),
    )
    dumped = str(vars(job))
    assert "93.184.216.34" not in dumped


def test_concurrency_limit_enforced_per_owner():
    raw = json.dumps(_collection()).encode()
    store = _store()
    settings = Settings(max_concurrent_jobs_per_owner=1)
    create_job(
        collection_raw=raw, collection_filename="c.json", environment_raw=None, environment_filename=None,
        folder_id=None, supplied_values={"host": "93.184.216.34"}, owner_key="127.0.0.1",
        idempotency_key=None, store=store, settings=settings,
    )
    with pytest.raises(ProblemException) as exc:
        create_job(
            collection_raw=raw, collection_filename="c.json", environment_raw=None, environment_filename=None,
            folder_id=None, supplied_values={"host": "93.184.216.34"}, owner_key="127.0.0.1",
            idempotency_key=None, store=store, settings=settings,
        )
    assert exc.value.status == 429


def test_idempotency_key_returns_existing_active_job_instead_of_creating_a_new_one():
    raw = json.dumps(_collection()).encode()
    store = _store()
    first = create_job(
        collection_raw=raw, collection_filename="c.json", environment_raw=None, environment_filename=None,
        folder_id=None, supplied_values={"host": "93.184.216.34"}, owner_key="127.0.0.1",
        idempotency_key="key-1", store=store, settings=Settings(),
    )
    second = create_job(
        collection_raw=raw, collection_filename="c.json", environment_raw=None, environment_filename=None,
        folder_id=None, supplied_values={"host": "93.184.216.34"}, owner_key="127.0.0.1",
        idempotency_key="key-1", store=store, settings=Settings(),
    )
    assert second.id == first.id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_execution_job_service_create.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.execution_job_service'`

- [ ] **Step 3: Implement `create_job`**

```python
# backend/app/services/execution_job_service.py
"""Create and drive execution jobs through their state machine.

`create_job` is synchronous: it parses, resolves, and gates a submission
before ever persisting a job, so a rejected submission never creates job
state a client would need to clean up. `run_job` (Task 6) does the actual
state-machine driving via an injected NewmanRunner.
"""

from __future__ import annotations

import time

from ..core.config import Settings
from ..core.errors import rate_limited, validation_error
from ..core.security import new_analysis_id
from ..domain.execution_job import ExecutionJob, ExecutionJobState
from ..repositories.execution_job_store import ExecutionJobStore
from .postman_parser import parse_collection, parse_environment
from .postman_variable_extractor import extract_variable_references
from .postman_variable_resolver import resolve_variables, unresolved_names


def create_job(
    *,
    collection_raw: bytes,
    collection_filename: str,
    environment_raw: bytes | None,
    environment_filename: str | None,
    folder_id: str | None,
    supplied_values: dict[str, str],
    owner_key: str,
    idempotency_key: str | None,
    store: ExecutionJobStore,
    settings: Settings,
) -> ExecutionJob:
    if idempotency_key:
        existing = store.find_by_idempotency_key(owner_key, idempotency_key)
        if existing is not None:
            return existing

    if store.count_active(owner_key) >= settings.max_concurrent_jobs_per_owner:
        raise rate_limited(
            f"Too many active execution jobs ({settings.max_concurrent_jobs_per_owner} allowed at a time)."
        )

    parsed_collection = parse_collection(collection_raw, filename=collection_filename, settings=settings)
    collection_data = parsed_collection.data

    environment_values: dict[str, str] = {}
    if environment_raw is not None:
        parsed_env = parse_environment(
            environment_raw, filename=environment_filename or "environment.json", settings=settings,
        )
        environment_values = parsed_env.values

    collection_variables = {
        v.get("key"): str(v.get("value", ""))
        for v in (collection_data.get("variable") or [])
        if isinstance(v, dict) and v.get("key")
    }

    references = extract_variable_references(collection_data)
    variables = resolve_variables(
        references,
        collection_variables=collection_variables,
        environment_values=environment_values,
        supplied=supplied_values,
    )
    unresolved = unresolved_names(variables)
    if unresolved:
        raise validation_error(
            f"Cannot start execution: unresolved variables {', '.join(unresolved)}.",
            errors=[{"path": "$.supplied_values", "detail": f"Missing value for '{name}'."} for name in unresolved],
        )

    now = time.time()
    job = ExecutionJob(
        id=new_analysis_id(),
        owner_key=owner_key,
        collection_name=str(collection_data.get("info", {}).get("name") or "Untitled collection"),
        created_at=now,
        expires_at=now + settings.job_ttl_seconds,
        idempotency_key=idempotency_key,
        state=ExecutionJobState.QUEUED,
        warnings=list(parsed_collection.warnings),
    )
    store.create(job)
    return job
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_execution_job_service_create.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/execution_job_service.py backend/tests/unit/test_execution_job_service_create.py
git commit -m "feat(execution-job): validate, resolve, and gate job creation"
```

---

### Task 6: `run_job` — drive the state machine with an injected `NewmanRunner`

**Files:**
- Modify: `backend/app/services/execution_job_service.py`
- Test: `backend/tests/unit/test_execution_job_service_run.py`

**Interfaces:**
- Consumes: `NewmanRunner`, `RunInput`, `RunOutcome` (Task 1); `FakeNewmanRunner` (Task 2, tests only); `ExecutionJobStore` (Task 4); `analysis_service.build_analysis` (existing, unchanged)
- Produces: `run_job(job_id: str, *, collection_data: dict, environment_data: dict | None, variable_values: dict[str, str], folder_id: str | None, runner: NewmanRunner, store: ExecutionJobStore, settings: Settings) -> None`. Task 10's API endpoint schedules this via `BackgroundTasks`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_execution_job_service_run.py
import json
import time

from app.core.config import Settings
from app.domain.execution_job import ExecutionJob, ExecutionJobState, RunOutcome
from app.repositories.execution_job_store import InMemoryExecutionJobStore
from app.services.execution_job_service import run_job
from app.services.fake_newman_runner import FakeNewmanRunner
from tests.fixtures import builders as b


def _queued_job(store) -> ExecutionJob:
    job = ExecutionJob(
        id="job_1", owner_key="127.0.0.1", collection_name="Demo",
        created_at=time.time(), expires_at=time.time() + 60,
    )
    store.create(job)
    return job


def _newman_report(token: str) -> bytes:
    return json.dumps(b.report("Login Flow", [
        b.execution("Login", "POST", "https://api.example.com/login",
                    resp_body={"token": token}, position=0),
        b.execution("Profile", "GET", "https://api.example.com/me",
                    req_headers=[b.header("Authorization", f"Bearer {token}")], position=1),
    ])).encode()


def test_successful_run_reaches_ready_with_an_analysis_id():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    runner = FakeNewmanRunner([
        RunOutcome(success=True, report_bytes=_newman_report("tok_AAA111")),
        RunOutcome(success=True, report_bytes=_newman_report("tok_ZZZ999")),
    ])
    run_job(
        job.id, collection_data={"info": {"name": "Demo"}, "item": []}, environment_data=None,
        variable_values={}, folder_id=None, runner=runner, store=store, settings=Settings(),
    )
    final = store.get(job.id)
    assert final.state == ExecutionJobState.READY
    assert final.analysis_id is not None
    assert final.stage_history == [
        "validating", "running_baseline", "running_comparison", "analyzing", "ready",
    ]
    assert len(runner.calls) == 2


def test_baseline_failure_stops_before_comparison_and_marks_failed():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    runner = FakeNewmanRunner([RunOutcome(success=False, error_code="timeout", error_detail="Run exceeded 5m.")])
    run_job(
        job.id, collection_data={"info": {"name": "Demo"}, "item": []}, environment_data=None,
        variable_values={}, folder_id=None, runner=runner, store=store, settings=Settings(),
    )
    final = store.get(job.id)
    assert final.state == ExecutionJobState.FAILED
    assert final.error_code == "timeout"
    assert final.analysis_id is None
    assert len(runner.calls) == 1  # comparison never attempted


def test_comparison_failure_marks_failed_after_baseline_succeeded():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    runner = FakeNewmanRunner([
        RunOutcome(success=True, report_bytes=_newman_report("tok_AAA111")),
        RunOutcome(success=False, error_code="process_failed", error_detail="Newman exited 1."),
    ])
    run_job(
        job.id, collection_data={"info": {"name": "Demo"}, "item": []}, environment_data=None,
        variable_values={}, folder_id=None, runner=runner, store=store, settings=Settings(),
    )
    final = store.get(job.id)
    assert final.state == ExecutionJobState.FAILED
    assert final.error_code == "process_failed"


def test_cancel_requested_before_run_stops_immediately():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    job.cancel_requested = True
    store.update(job)
    runner = FakeNewmanRunner([])
    run_job(
        job.id, collection_data={"info": {"name": "Demo"}, "item": []}, environment_data=None,
        variable_values={}, folder_id=None, runner=runner, store=store, settings=Settings(),
    )
    assert store.get(job.id).state == ExecutionJobState.CANCELLED
    assert runner.calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_execution_job_service_run.py -v`
Expected: FAIL — `ImportError: cannot import name 'run_job'`

- [ ] **Step 3: Implement `run_job`**

Append to `backend/app/services/execution_job_service.py`. Add these imports at the top of the file alongside the existing ones:

```python
from ..domain.execution_job import NewmanRunner, RunInput, RunOutcome
from . import analysis_service
```

Then append:

```python
def _advance(job, state, store) -> bool:
    """Move to `state` unless cancellation was requested; returns False if cancelled."""
    fresh = store.get(job.id) or job
    if fresh.cancel_requested:
        fresh.state = ExecutionJobState.CANCELLED
        store.update(fresh)
        return False
    fresh.state = state
    fresh.stage_history.append(state.value)
    store.update(fresh)
    return True


def run_job(
    job_id: str,
    *,
    collection_data: dict,
    environment_data: dict | None,
    variable_values: dict[str, str],
    folder_id: str | None,
    runner: NewmanRunner,
    store: ExecutionJobStore,
    settings: Settings,
) -> None:
    job = store.get(job_id)
    if job is None:
        return

    if not _advance(job, ExecutionJobState.VALIDATING, store):
        return
    if not _advance(job, ExecutionJobState.RUNNING_BASELINE, store):
        return

    run_input = RunInput(
        collection_data=collection_data, environment_data=environment_data,
        variable_values=variable_values, folder_id=folder_id,
        timeout_seconds=settings.job_run_timeout_seconds,
    )
    baseline: RunOutcome = runner.run(run_input)
    if not baseline.success:
        job = store.get(job_id) or job
        job.state = ExecutionJobState.FAILED
        job.error_code = baseline.error_code
        job.error_detail = baseline.error_detail
        store.update(job)
        return

    if not _advance(job, ExecutionJobState.RUNNING_COMPARISON, store):
        return
    comparison: RunOutcome = runner.run(run_input)
    if not comparison.success:
        job = store.get(job_id) or job
        job.state = ExecutionJobState.FAILED
        job.error_code = comparison.error_code
        job.error_detail = comparison.error_detail
        store.update(job)
        return

    if not _advance(job, ExecutionJobState.ANALYZING, store):
        return

    analysis = analysis_service.build_analysis(
        [("baseline.json", baseline.report_bytes), ("comparison.json", comparison.report_bytes)], settings,
    )
    # The (unrelated) analysis-session store is looked up via the shared FastAPI
    # dependency in the API layer, not here - Task 7 wires the two together.
    job = store.get(job_id) or job
    job.state = ExecutionJobState.READY
    job.stage_history.append(ExecutionJobState.READY.value)
    job.analysis_id = analysis.id
    store.update(job)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_execution_job_service_run.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/execution_job_service.py backend/tests/unit/test_execution_job_service_run.py
git commit -m "feat(execution-job): drive job state machine via injected NewmanRunner"
```

---

### Task 7: Wire `run_job`'s analysis into the existing analysis session store

**Files:**
- Modify: `backend/app/services/execution_job_service.py`
- Test: `backend/tests/unit/test_execution_job_service_run.py` (extend)

**Interfaces:**
- Consumes: `SessionStore` (existing, `app/repositories/analysis_store.py`)
- Produces: `run_job(...)` gains one more keyword parameter, `analysis_store: SessionStore`, and persists the built `Analysis` into it before marking the job `READY`. Task 10/11 read the resulting `analysis_id` back out through the existing `GET /api/v1/analyses/{id}` endpoint, unchanged.

- [ ] **Step 1: Write the failing test (extends the existing file)**

Add to `backend/tests/unit/test_execution_job_service_run.py`:

```python
from app.repositories.analysis_store import InMemorySessionStore


def test_successful_run_persists_analysis_into_the_analysis_store():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    analysis_store = InMemorySessionStore(ttl_seconds=60)
    job = _queued_job(store)
    runner = FakeNewmanRunner([
        RunOutcome(success=True, report_bytes=_newman_report("tok_AAA111")),
        RunOutcome(success=True, report_bytes=_newman_report("tok_ZZZ999")),
    ])
    run_job(
        job.id, collection_data={"info": {"name": "Demo"}, "item": []}, environment_data=None,
        variable_values={}, folder_id=None, runner=runner, store=store,
        analysis_store=analysis_store, settings=Settings(),
    )
    final = store.get(job.id)
    persisted = analysis_store.get(final.analysis_id)
    assert persisted is not None
    assert persisted.id == final.analysis_id
```

Update the four existing calls to `run_job(...)` in this file (the success, baseline-failure, comparison-failure, and cancel tests) to also pass `analysis_store=InMemorySessionStore(ttl_seconds=60)` as a keyword argument — the failing-path tests never reach the point of using it, but the signature now requires it.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_execution_job_service_run.py -v`
Expected: FAIL — `TypeError: run_job() got an unexpected keyword argument 'analysis_store'`

- [ ] **Step 3: Add the parameter and the persist call**

In `backend/app/services/execution_job_service.py`, add this import at the top:

```python
from ..repositories.analysis_store import SessionStore
```

Change `run_job`'s signature to add `analysis_store: SessionStore,` (place it right after `store: ExecutionJobStore,`), and change the final block from:

```python
    analysis = analysis_service.build_analysis(
        [("baseline.json", baseline.report_bytes), ("comparison.json", comparison.report_bytes)], settings,
    )
    # The (unrelated) analysis-session store is looked up via the shared FastAPI
    # dependency in the API layer, not here - Task 7 wires the two together.
    job = store.get(job_id) or job
```

to:

```python
    analysis = analysis_service.build_analysis(
        [("baseline.json", baseline.report_bytes), ("comparison.json", comparison.report_bytes)], settings,
    )
    analysis_store.create(analysis)
    job = store.get(job_id) or job
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_execution_job_service_run.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/execution_job_service.py backend/tests/unit/test_execution_job_service_run.py
git commit -m "feat(execution-job): persist the built analysis into the existing session store"
```

---

### Task 8: `deps.py` additions — job store and ownership dependency

**Files:**
- Modify: `backend/app/api/deps.py`
- Test: `backend/tests/unit/test_execution_job_deps.py`

**Interfaces:**
- Consumes: `InMemoryExecutionJobStore` (Task 4)
- Produces: `get_job_store() -> ExecutionJobStore` (cached, mirrors `get_store`); `get_owner_key(request: Request) -> str`; `require_owned_job(job_id: str, request: Request, store=Depends(get_job_store)) -> ExecutionJob` (raises `not_found` if missing OR owned by a different `owner_key` — never distinguishes the two in the response). Task 10/11 (the API endpoints) all depend on these.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_execution_job_deps.py
import time
from unittest.mock import Mock

import pytest

from app.api.deps import get_owner_key, require_owned_job
from app.core.errors import ProblemException
from app.domain.execution_job import ExecutionJob
from app.repositories.execution_job_store import InMemoryExecutionJobStore


def _request(host: str | None):
    req = Mock()
    req.client = Mock(host=host) if host else None
    return req


def test_get_owner_key_uses_client_host():
    assert get_owner_key(_request("10.0.0.5")) == "10.0.0.5"


def test_get_owner_key_falls_back_when_no_client():
    assert get_owner_key(_request(None)) == "unknown"


def test_require_owned_job_returns_job_for_matching_owner():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = ExecutionJob(id="j1", owner_key="10.0.0.5", collection_name="d",
                        created_at=time.time(), expires_at=time.time() + 60)
    store.create(job)
    result = require_owned_job("j1", _request("10.0.0.5"), store)
    assert result is job


def test_require_owned_job_404s_for_missing_job():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    with pytest.raises(ProblemException) as exc:
        require_owned_job("missing", _request("10.0.0.5"), store)
    assert exc.value.status == 404


def test_require_owned_job_404s_for_mismatched_owner_not_403():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = ExecutionJob(id="j1", owner_key="10.0.0.5", collection_name="d",
                        created_at=time.time(), expires_at=time.time() + 60)
    store.create(job)
    with pytest.raises(ProblemException) as exc:
        require_owned_job("j1", _request("10.0.0.9"), store)
    assert exc.value.status == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_execution_job_deps.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_owner_key'`

- [ ] **Step 3: Add the dependencies**

Add these imports at the top of `backend/app/api/deps.py`, alongside the existing ones:

```python
from ..domain.execution_job import ExecutionJob
from ..repositories.execution_job_store import ExecutionJobStore, InMemoryExecutionJobStore
```

Append to the file:

```python
@lru_cache
def get_job_store() -> ExecutionJobStore:
    settings = get_settings()
    return InMemoryExecutionJobStore(ttl_seconds=settings.job_ttl_seconds)


def get_owner_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def require_owned_job(
    job_id: str, request: Request, store: ExecutionJobStore = Depends(get_job_store),
) -> ExecutionJob:
    job = store.get(job_id)
    if job is None or job.owner_key != get_owner_key(request):
        raise not_found("Execution job not found or expired.")
    return job
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_execution_job_deps.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/deps.py backend/tests/unit/test_execution_job_deps.py
git commit -m "feat(execution-job): add job-store and ownership dependencies"
```

---

### Task 9: Schemas and presenter for job responses

**Files:**
- Modify: `backend/app/schemas/presenters.py`
- Test: `backend/tests/unit/test_execution_job_presenters.py`

**Interfaces:**
- Consumes: `ExecutionJob` (Task 1)
- Produces: `execution_job_dto(job: ExecutionJob) -> dict` returning exactly `{"job_id", "state", "stage_history", "warnings", "error_code", "error_detail", "analysis_id"}`. Task 10 uses this.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_execution_job_presenters.py
import time

from app.domain.execution_job import ExecutionJob, ExecutionJobState
from app.schemas.presenters import execution_job_dto


def test_execution_job_dto_shape():
    job = ExecutionJob(
        id="j1", owner_key="127.0.0.1", collection_name="Demo",
        created_at=time.time(), expires_at=time.time() + 60,
        state=ExecutionJobState.READY, analysis_id="a1",
        stage_history=["validating", "running_baseline"], warnings=["schema unknown"],
    )
    dto = execution_job_dto(job)
    assert dto == {
        "job_id": "j1",
        "state": "ready",
        "stage_history": ["validating", "running_baseline"],
        "warnings": ["schema unknown"],
        "error_code": None,
        "error_detail": None,
        "analysis_id": "a1",
    }


def test_execution_job_dto_never_includes_owner_key_or_collection_data():
    job = ExecutionJob(id="j1", owner_key="127.0.0.1", collection_name="Demo",
                        created_at=time.time(), expires_at=time.time() + 60)
    dto = execution_job_dto(job)
    assert "owner_key" not in dto
    assert "127.0.0.1" not in str(dto)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_execution_job_presenters.py -v`
Expected: FAIL — `ImportError: cannot import name 'execution_job_dto'`

- [ ] **Step 3: Add the presenter**

Add this import at the top of `backend/app/schemas/presenters.py`, alongside the existing ones:

```python
from ..domain.execution_job import ExecutionJob
```

Append:

```python
def execution_job_dto(job: ExecutionJob) -> dict:
    return {
        "job_id": job.id,
        "state": job.state.value,
        "stage_history": job.stage_history,
        "warnings": job.warnings,
        "error_code": job.error_code,
        "error_detail": job.error_detail,
        "analysis_id": job.analysis_id,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_execution_job_presenters.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/presenters.py backend/tests/unit/test_execution_job_presenters.py
git commit -m "feat(execution-job): add job response presenter"
```

---

### Task 10: `POST /api/v1/execution-jobs` and `GET /api/v1/execution-jobs/{job_id}`

**Files:**
- Modify: `backend/app/api/v1/execution_jobs.py`

**Interfaces:**
- Consumes: `create_job` (Task 5); `execution_job_dto` (Task 9); `get_job_store`, `get_owner_key`, `require_owned_job` (Task 8); `get_store` (existing, the analysis `SessionStore` dependency); `FakeNewmanRunner` (Task 2, wired as the only runner for this phase — Phase 3 replaces this with a real implementation via a settings-driven factory, out of scope here)
- Produces: `POST /execution-jobs` returning `202` with `execution_job_dto`'s shape plus a `status_url`; `GET /execution-jobs/{job_id}` returning `execution_job_dto`'s shape. No new automated test in this task — Task 12 adds HTTP-level integration tests for the whole lifecycle across Tasks 10 and 11 together.

- [ ] **Step 1: Implement the endpoints**

At the top of `backend/app/api/v1/execution_jobs.py`, change the existing:

```python
from fastapi import APIRouter, Depends, File, UploadFile
```

to:

```python
import json

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, UploadFile
```

and add these new imports alongside the existing `from ...` block:

```python
from ...core.errors import validation_error
from ...domain.execution_job import ExecutionJob, ExecutionJobState, RunOutcome
from ...repositories.analysis_store import SessionStore
from ...repositories.execution_job_store import ExecutionJobStore
from ...services import execution_job_service
from ...services.fake_newman_runner import FakeNewmanRunner
from ...services.postman_parser import parse_collection, parse_environment
from ..deps import get_job_store, get_owner_key, get_store, require_owned_job
```

Append these two endpoints to the router:

```python
def _default_runner() -> FakeNewmanRunner:
    """Phase 2 stand-in: two canned successful outcomes with no dynamic data.

    Phase 3 replaces this with a settings-driven factory selecting a real
    Docker/ECS NewmanRunner. Left deliberately simple here - this phase's job
    is proving the state machine and API surface, not producing meaningful
    correlation results by default.
    """
    empty_report = json.dumps({"run": {"executions": [
        {"item": {"name": "Ping"}, "request": {"method": "GET", "url": "https://example.com/"},
         "response": {"code": 200, "status": "OK", "responseTime": 1, "header": [], "body": "{}",
                      "stream": {"type": "Buffer", "data": []}}},
    ]}}).encode()
    return FakeNewmanRunner([
        RunOutcome(success=True, report_bytes=empty_report),
        RunOutcome(success=True, report_bytes=empty_report),
    ])


@router.post("", status_code=202)
async def create_execution_job(
    background_tasks: BackgroundTasks,
    collection: UploadFile = File(...),
    environment: UploadFile | None = File(None),
    folder_id: str | None = Form(None),
    supplied_values_json: str = Form("{}"),
    confirm: bool = Form(...),
    settings: Settings = Depends(get_settings),
    job_store: ExecutionJobStore = Depends(get_job_store),
    analysis_store: SessionStore = Depends(get_store),
    owner_key: str = Depends(get_owner_key),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    _: None = Depends(enforce_rate_limit),
) -> dict:
    if not confirm:
        raise validation_error("Execution requires explicit confirmation (confirm=true).")
    try:
        supplied_values = json.loads(supplied_values_json)
    except json.JSONDecodeError as exc:
        raise validation_error("'supplied_values_json' is not valid JSON.") from exc
    if not isinstance(supplied_values, dict):
        raise validation_error("'supplied_values_json' must be a JSON object.")
    supplied_values = {str(k): str(v) for k, v in supplied_values.items()}

    collection_raw = await collection.read()
    environment_raw = await environment.read() if environment is not None else None
    environment_filename = environment.filename if environment is not None else None
    collection_filename = collection.filename or "collection.json"

    job = execution_job_service.create_job(
        collection_raw=collection_raw,
        collection_filename=collection_filename,
        environment_raw=environment_raw,
        environment_filename=environment_filename,
        folder_id=folder_id,
        supplied_values=supplied_values,
        owner_key=owner_key,
        idempotency_key=idempotency_key,
        store=job_store,
        settings=settings,
    )

    if job.state == ExecutionJobState.QUEUED:
        parsed_collection = parse_collection(collection_raw, filename=collection_filename, settings=settings)
        collection_variables = {
            v.get("key"): str(v.get("value", ""))
            for v in (parsed_collection.data.get("variable") or [])
            if isinstance(v, dict) and v.get("key")
        }
        environment_data = None
        environment_values: dict[str, str] = {}
        if environment_raw is not None:
            parsed_env = parse_environment(
                environment_raw, filename=environment_filename or "environment.json", settings=settings,
            )
            environment_data = parsed_env.data
            environment_values = parsed_env.values
        variable_values = {**collection_variables, **environment_values, **supplied_values}

        background_tasks.add_task(
            execution_job_service.run_job,
            job.id,
            collection_data=parsed_collection.data,
            environment_data=environment_data,
            variable_values=variable_values,
            folder_id=folder_id,
            runner=_default_runner(),
            store=job_store,
            analysis_store=analysis_store,
            settings=settings,
        )

    log.info("execution job created", extra={"stage": "create_job", "job_id": job.id, "state": job.state.value})
    dto = presenters.execution_job_dto(job)
    dto["status_url"] = f"{settings.api_prefix}/execution-jobs/{job.id}"
    return dto


@router.get("/{job_id}")
async def get_execution_job(job: ExecutionJob = Depends(require_owned_job)) -> dict:
    return presenters.execution_job_dto(job)
```

The `if job.state == ExecutionJobState.QUEUED:` guard matters because `create_job` returns an *existing* job unchanged on an idempotency-key hit — that job may already be past `QUEUED` (e.g. already `READY` from an earlier call), in which case a background task must not be scheduled again.

- [ ] **Step 2: Manually smoke-test the lifecycle**

Run: `cd backend && ./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000 &` then:

```bash
curl -s -F 'collection=@/dev/stdin;filename=c.json;type=application/json' \
  -F 'confirm=true' -F 'supplied_values_json={}' \
  http://127.0.0.1:8000/api/v1/execution-jobs \
  <<< '{"info":{"name":"Smoke","schema":"https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},"item":[]}'
```

Expected: `202` with `"state": "queued"` and a `status_url`. Then `curl http://127.0.0.1:8000/api/v1/execution-jobs/<job_id>` a moment later should show `"state": "ready"` with a non-null `analysis_id` (the fake runner completes near-instantly). Stop uvicorn afterward.

- [ ] **Step 3: Commit**

```bash
git add backend/app/api/v1/execution_jobs.py
git commit -m "feat(api): add POST/GET /api/v1/execution-jobs"
```

---

### Task 11: `DELETE /api/v1/execution-jobs/{job_id}`

**Files:**
- Modify: `backend/app/api/v1/execution_jobs.py`

**Interfaces:**
- Consumes: `require_owned_job`, `get_job_store` (Task 8)
- Produces: `DELETE /execution-jobs/{job_id}` returning `204`. Task 12 tests this over HTTP.

- [ ] **Step 1: Implement the endpoint**

Append to `backend/app/api/v1/execution_jobs.py`:

```python
@router.delete("/{job_id}", status_code=204)
async def delete_execution_job(
    job: ExecutionJob = Depends(require_owned_job),
    job_store: ExecutionJobStore = Depends(get_job_store),
) -> None:
    if job.is_active:
        job.cancel_requested = True
        job.state = ExecutionJobState.CANCELLED
        job_store.update(job)
    else:
        job_store.delete(job.id)
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/api/v1/execution_jobs.py
git commit -m "feat(api): add DELETE /api/v1/execution-jobs/{job_id}"
```

---

### Task 12: Integration tests for the full job lifecycle

**Files:**
- Create: `backend/tests/integration/test_execution_jobs_lifecycle.py`

**Interfaces:**
- Consumes: everything from Tasks 1-11, exercised only through the public HTTP surface (`TestClient`).

- [ ] **Step 1: Write the integration tests**

```python
# backend/tests/integration/test_execution_jobs_lifecycle.py
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.fixtures.postman_builders import pm_collection, pm_request


@pytest.fixture
def client():
    return TestClient(create_app())


def _collection():
    return pm_collection("Smoke", [pm_request("Ping", "GET", "https://93.184.216.34/ping")])


def _create(client, **form_overrides):
    form = {"confirm": "true", "supplied_values_json": "{}"}
    form.update(form_overrides)
    return client.post(
        "/api/v1/execution-jobs",
        data=form,
        files={"collection": ("c.json", json.dumps(_collection()).encode(), "application/json")},
    )


def test_create_job_returns_202_and_reaches_ready(client):
    resp = _create(client)
    assert resp.status_code == 202
    body = resp.json()
    assert body["state"] == "queued"
    job_id = body["job_id"]

    status = body
    for _ in range(20):
        status = client.get(f"/api/v1/execution-jobs/{job_id}").json()
        if status["state"] in ("ready", "failed"):
            break
        time.sleep(0.05)
    assert status["state"] == "ready"
    assert status["analysis_id"] is not None

    analysis_resp = client.get(f"/api/v1/analyses/{status['analysis_id']}")
    assert analysis_resp.status_code == 200


def test_create_job_rejects_without_confirmation(client):
    resp = _create(client, confirm="false")
    assert resp.status_code == 422


def test_get_unknown_job_is_404(client):
    resp = client.get("/api/v1/execution-jobs/does-not-exist")
    assert resp.status_code == 404


def test_delete_active_job_cancels_it(client):
    resp = _create(client)
    job_id = resp.json()["job_id"]
    delete_resp = client.delete(f"/api/v1/execution-jobs/{job_id}")
    assert delete_resp.status_code == 204


def test_idempotency_key_returns_the_same_job(client):
    headers = {"Idempotency-Key": "same-key"}
    first = client.post(
        "/api/v1/execution-jobs", data={"confirm": "true", "supplied_values_json": "{}"},
        files={"collection": ("c.json", json.dumps(_collection()).encode(), "application/json")},
        headers=headers,
    )
    second = client.post(
        "/api/v1/execution-jobs", data={"confirm": "true", "supplied_values_json": "{}"},
        files={"collection": ("c.json", json.dumps(_collection()).encode(), "application/json")},
        headers=headers,
    )
    assert first.json()["job_id"] == second.json()["job_id"]


def test_existing_inspect_and_analyses_endpoints_still_work(client):
    inspect_resp = client.post(
        "/api/v1/execution-jobs/inspect",
        files={"collection": ("c.json", json.dumps(_collection()).encode(), "application/json")},
    )
    assert inspect_resp.status_code == 200

    from tests.fixtures import builders as b
    scenario = b.report("Smoke", [b.execution("Ping", "GET", "https://api.example.com/ping", position=0)])
    analyses_resp = client.post(
        "/api/v1/analyses",
        files=[("files", ("baseline.json", json.dumps(scenario).encode(), "application/json"))],
    )
    assert analyses_resp.status_code == 201
```

- [ ] **Step 2: Run the new integration tests**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/integration/test_execution_jobs_lifecycle.py -v`
Expected: PASS (6 tests)

- [ ] **Step 3: Run the complete backend test suite**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest -v`
Expected: PASS — every test from Tasks 1-12 plus every pre-existing test (including Phase 1's) passes with zero failures.

- [ ] **Step 4: Run lint and type checks**

Run: `cd backend && ./.venv/Scripts/python.exe -m ruff check . && ./.venv/Scripts/python.exe -m mypy app`
Expected: both exit 0.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/integration/test_execution_jobs_lifecycle.py
git commit -m "test: integration coverage for the full execution-job lifecycle"
```

---

## Definition of Done for Phase 2

- A tester can `POST /api/v1/execution-jobs` with a resolved collection and get back a job id immediately (`202`), poll `GET /api/v1/execution-jobs/{id}` to watch it move through `queued → validating → running_baseline → running_comparison → analyzing → ready`, and land on the existing `GET /api/v1/analyses/{analysis_id}` unchanged.
- Unresolved variables block job creation with a specific 422 before any job is persisted.
- Ownership (placeholder: client host), per-owner concurrency limits, and idempotent resubmission are all enforced and tested.
- `DELETE` cancels an active job or deletes a terminal one; a job never becomes visible to, or cancellable by, a different owner.
- Phase 1's `/inspect` endpoint and the original `/api/v1/analyses` direct-upload endpoint are both regression-tested and still pass.
- No real Newman process, Docker container, or network execution happens anywhere in this phase — `FakeNewmanRunner` is the only implementation of `NewmanRunner` that exists. Phase 3 adds the real Docker-backed implementation behind the same interface.
