import json
import logging
import time

from app.core.config import Settings
from app.core.errors import validation_error
from app.core.logging import RedactingJsonFormatter
from app.domain.execution_job import ExecutionJob, ExecutionJobState, RunInput, RunOutcome
from app.repositories.analysis_store import InMemorySessionStore
from app.repositories.execution_job_store import InMemoryExecutionJobStore
from app.services import analysis_service
from app.services.execution_job_service import _redact_variable_values, run_job
from app.services.fake_newman_runner import FakeNewmanRunner
from tests.fixtures import builders as b
from tests.fixtures.postman_builders import pm_collection, pm_request


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


def _run(
    job, runner, store, *,
    analysis_store=None, settings=None, collection_data=None, resolver=None, variable_values=None,
    supplied_values=None, environment_data=None,
):
    run_job(
        job.id,
        collection_data=collection_data or {"info": {"name": "Demo"}, "item": []},
        environment_data=environment_data,
        variable_values=variable_values if variable_values is not None else {},
        supplied_values=supplied_values if supplied_values is not None else {},
        folder_id=None,
        runner=runner,
        store=store,
        analysis_store=analysis_store or InMemorySessionStore(ttl_seconds=60),
        settings=settings or Settings(),
        resolver=resolver,
    )


# --- Happy path / basic failure mapping (Tasks 6 + 7 briefs) ---


def test_successful_run_reaches_ready_with_an_analysis_id():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    runner = FakeNewmanRunner([
        RunOutcome(success=True, report_bytes=_newman_report("tok_AAA111")),
        RunOutcome(success=True, report_bytes=_newman_report("tok_ZZZ999")),
    ])
    _run(job, runner, store)
    final = store.get(job.id)
    assert final.state == ExecutionJobState.READY
    assert final.analysis_id is not None
    assert final.stage_history == [
        "validating", "running_baseline", "running_comparison", "analyzing", "ready",
    ]
    assert len(runner.calls) == 2


def test_successful_run_persists_analysis_into_the_analysis_store():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    analysis_store = InMemorySessionStore(ttl_seconds=60)
    job = _queued_job(store)
    runner = FakeNewmanRunner([
        RunOutcome(success=True, report_bytes=_newman_report("tok_AAA111")),
        RunOutcome(success=True, report_bytes=_newman_report("tok_ZZZ999")),
    ])
    _run(job, runner, store, analysis_store=analysis_store)
    final = store.get(job.id)
    persisted = analysis_store.get(final.analysis_id)
    assert persisted is not None
    assert persisted.id == final.analysis_id


def test_baseline_failure_stops_before_comparison_and_marks_failed():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    runner = FakeNewmanRunner([RunOutcome(success=False, error_code="timeout", error_detail="Run exceeded 5m.")])
    _run(job, runner, store)
    final = store.get(job.id)
    assert final.state == ExecutionJobState.FAILED
    assert final.error_code == "timeout"
    assert final.analysis_id is None
    assert final.stage_history[-1] == "failed"
    assert len(runner.calls) == 1  # comparison never attempted


def test_comparison_failure_marks_failed_after_baseline_succeeded():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    runner = FakeNewmanRunner([
        RunOutcome(success=True, report_bytes=_newman_report("tok_AAA111")),
        RunOutcome(success=False, error_code="process_failed", error_detail="Newman exited 1."),
    ])
    _run(job, runner, store)
    final = store.get(job.id)
    assert final.state == ExecutionJobState.FAILED
    assert final.error_code == "process_failed"
    assert final.stage_history[-1] == "failed"


def test_cancel_requested_before_run_stops_immediately():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    job.cancel_requested = True
    store.update(job)
    runner = FakeNewmanRunner([])
    _run(job, runner, store)
    final = store.get(job.id)
    assert final.state == ExecutionJobState.CANCELLED
    assert final.stage_history[-1] == "cancelled"
    assert runner.calls == []


# --- A5.1: start guard ---


def test_missing_job_is_a_noop():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    runner = FakeNewmanRunner([])
    run_job(
        "does-not-exist", collection_data={"info": {"name": "Demo"}, "item": []}, environment_data=None,
        variable_values={}, supplied_values={}, folder_id=None, runner=runner, store=store,
        analysis_store=InMemorySessionStore(ttl_seconds=60), settings=Settings(),
    )
    assert store.get("does-not-exist") is None
    assert runner.calls == []


def test_non_queued_active_job_is_a_noop():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    job.state = ExecutionJobState.RUNNING_BASELINE
    store.update(job)
    runner = FakeNewmanRunner([])
    _run(job, runner, store)
    final = store.get(job.id)
    assert final.state == ExecutionJobState.RUNNING_BASELINE
    assert runner.calls == []


def test_already_terminal_job_is_a_noop():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    job.state = ExecutionJobState.READY
    job.analysis_id = "an_existing"
    store.update(job)
    runner = FakeNewmanRunner([])
    _run(job, runner, store)
    final = store.get(job.id)
    assert final.state == ExecutionJobState.READY
    assert final.analysis_id == "an_existing"
    assert runner.calls == []


# --- A5.2: every state write re-reads; never resurrect/overwrite ---


class _CancellingRunner:
    """Simulates a cancellation request arriving while the baseline run executes."""

    def __init__(self, store, job_id, outcome):
        self.store = store
        self.job_id = job_id
        self.outcome = outcome
        self.calls: list[RunInput] = []

    def run(self, run_input: RunInput, *, should_cancel=None) -> RunOutcome:
        self.calls.append(run_input)
        job = self.store.get(self.job_id)
        job.cancel_requested = True
        self.store.update(job)
        return self.outcome


def test_cancel_requested_during_baseline_run_stops_before_comparison():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    analysis_store = InMemorySessionStore(ttl_seconds=60)
    created: list = []
    analysis_store.create = lambda analysis: created.append(analysis)  # type: ignore[method-assign]
    job = _queued_job(store)
    runner = _CancellingRunner(store, job.id, RunOutcome(success=True, report_bytes=_newman_report("tok_AAA111")))
    _run(job, runner, store, analysis_store=analysis_store)
    final = store.get(job.id)
    assert final.state == ExecutionJobState.CANCELLED
    assert final.analysis_id is None
    assert len(runner.calls) == 1  # comparison never run
    assert created == []  # no analysis was ever built or persisted


class _DeletingRunner:
    """Simulates the job being deleted (e.g. via DELETE /jobs/{id}) mid-run."""

    def __init__(self, store, job_id, outcome):
        self.store = store
        self.job_id = job_id
        self.outcome = outcome
        self.calls: list[RunInput] = []

    def run(self, run_input: RunInput, *, should_cancel=None) -> RunOutcome:
        self.calls.append(run_input)
        self.store.delete(self.job_id)
        return self.outcome


def test_job_deleted_during_baseline_run_leaves_it_deleted():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    runner = _DeletingRunner(store, job.id, RunOutcome(success=True, report_bytes=_newman_report("tok_AAA111")))
    _run(job, runner, store)
    assert store.get(job.id) is None
    assert len(runner.calls) == 1  # never resurrected, comparison never run


class _TerminalWritingRunner:
    """Simulates some other path terminating the job (not via cancel_requested)
    while the baseline run executes.
    """

    def __init__(self, store, job_id, outcome):
        self.store = store
        self.job_id = job_id
        self.outcome = outcome
        self.calls: list[RunInput] = []

    def run(self, run_input: RunInput, *, should_cancel=None) -> RunOutcome:
        self.calls.append(run_input)
        job = self.store.get(self.job_id)
        job.state = ExecutionJobState.FAILED
        job.error_code = "external_failure"
        self.store.update(job)
        return self.outcome


def test_already_terminal_job_found_on_rewrite_is_never_overwritten():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    runner = _TerminalWritingRunner(store, job.id, RunOutcome(success=True, report_bytes=_newman_report("tok")))
    _run(job, runner, store)
    final = store.get(job.id)
    assert final.state == ExecutionJobState.FAILED
    assert final.error_code == "external_failure"  # not clobbered by run_job's own writes
    assert len(runner.calls) == 1  # comparison never run


# --- A5.3: VALIDATING does real destination validation ---


def test_blocked_destination_fails_closed_without_calling_the_runner():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    collection = pm_collection("Demo", [pm_request("Ping", "GET", "https://127.0.0.1/x")])
    runner = FakeNewmanRunner([])
    _run(job, runner, store, collection_data=collection)
    final = store.get(job.id)
    assert final.state == ExecutionJobState.FAILED
    assert final.error_code == "destination_validation_failed"
    assert final.error_detail == "One or more request targets failed destination validation."
    assert final.warnings  # the domain report's warnings were recorded
    assert final.stage_history[-1] == "failed"
    assert runner.calls == []


def test_public_ip_literal_destination_proceeds_without_dns():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    collection = pm_collection("Demo", [pm_request("Ping", "GET", "https://93.184.216.34/x")])
    runner = FakeNewmanRunner([
        RunOutcome(success=True, report_bytes=_newman_report("tok_AAA111")),
        RunOutcome(success=True, report_bytes=_newman_report("tok_ZZZ999")),
    ])
    _run(job, runner, store, collection_data=collection)
    final = store.get(job.id)
    assert final.state == ExecutionJobState.READY
    assert len(runner.calls) == 2


# --- A5.4: failure mapping ---


class _RaisingRunner:
    def __init__(self) -> None:
        self.calls: list[RunInput] = []

    def run(self, run_input: RunInput, *, should_cancel=None) -> RunOutcome:
        self.calls.append(run_input)
        raise RuntimeError("secret_token=abc123 leaked here")


def test_runner_exception_marks_failed_with_a_generic_detail():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    runner = _RaisingRunner()
    _run(job, runner, store)
    final = store.get(job.id)
    assert final.state == ExecutionJobState.FAILED
    assert final.error_code == "runner_error"
    assert final.error_detail == "The Newman runner failed unexpectedly."
    assert final.stage_history[-1] == "failed"
    assert "secret_token" not in (final.error_detail or "")


def test_run_failure_without_an_error_code_maps_to_run_failed():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    runner = FakeNewmanRunner([RunOutcome(success=False, error_code=None, error_detail="unspecified")])
    _run(job, runner, store)
    final = store.get(job.id)
    assert final.state == ExecutionJobState.FAILED
    assert final.error_code == "run_failed"
    assert final.stage_history[-1] == "failed"


def test_missing_report_bytes_marks_failed():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    runner = FakeNewmanRunner([RunOutcome(success=True, report_bytes=None)])
    _run(job, runner, store)
    final = store.get(job.id)
    assert final.state == ExecutionJobState.FAILED
    assert final.error_code == "missing_report"
    assert final.stage_history[-1] == "failed"


def test_empty_report_bytes_marks_failed():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    runner = FakeNewmanRunner([RunOutcome(success=True, report_bytes=b"")])
    _run(job, runner, store)
    final = store.get(job.id)
    assert final.state == ExecutionJobState.FAILED
    assert final.error_code == "missing_report"
    assert final.stage_history[-1] == "failed"


def test_oversized_report_bytes_marks_failed():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    runner = FakeNewmanRunner([RunOutcome(success=True, report_bytes=b"x" * 100)])
    _run(job, runner, store, settings=Settings(max_report_bytes=10))
    final = store.get(job.id)
    assert final.state == ExecutionJobState.FAILED
    assert final.error_code == "report_too_large"
    assert final.stage_history[-1] == "failed"


def test_analysis_problem_exception_maps_its_code_and_detail(monkeypatch):
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    runner = FakeNewmanRunner([
        RunOutcome(success=True, report_bytes=_newman_report("tok_AAA111")),
        RunOutcome(success=True, report_bytes=_newman_report("tok_ZZZ999")),
    ])

    def _boom(files, settings):
        raise validation_error("Bad newman report shape.")

    monkeypatch.setattr(analysis_service, "build_analysis", _boom)
    _run(job, runner, store)
    final = store.get(job.id)
    assert final.state == ExecutionJobState.FAILED
    assert final.error_code == "validation_error"
    assert final.error_detail == "Bad newman report shape."
    assert final.stage_history[-1] == "failed"


def test_analysis_unexpected_exception_marks_failed_with_a_generic_detail(monkeypatch):
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    runner = FakeNewmanRunner([
        RunOutcome(success=True, report_bytes=_newman_report("tok_AAA111")),
        RunOutcome(success=True, report_bytes=_newman_report("tok_ZZZ999")),
    ])

    def _boom(files, settings):
        raise RuntimeError("db password hunter2 in traceback")

    monkeypatch.setattr(analysis_service, "build_analysis", _boom)
    _run(job, runner, store)
    final = store.get(job.id)
    assert final.state == ExecutionJobState.FAILED
    assert final.error_code == "analysis_error"
    assert final.error_detail == "Analysis failed unexpectedly."
    assert final.stage_history[-1] == "failed"
    assert "hunter2" not in (final.error_detail or "")


def test_analysis_persisted_before_ready_state_is_written(monkeypatch):
    """analysis_store.create must happen before the job flips to READY, so a
    reader who observes READY can always find the analysis (Task 7 / A5.5).
    """
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    analysis_store = InMemorySessionStore(ttl_seconds=60)
    job = _queued_job(store)
    runner = FakeNewmanRunner([
        RunOutcome(success=True, report_bytes=_newman_report("tok_AAA111")),
        RunOutcome(success=True, report_bytes=_newman_report("tok_ZZZ999")),
    ])
    observed_states_at_create = []
    original_create = analysis_store.create

    def _spy_create(analysis):
        observed_states_at_create.append(store.get(job.id).state)
        original_create(analysis)

    monkeypatch.setattr(analysis_store, "create", _spy_create)
    _run(job, runner, store, analysis_store=analysis_store)
    assert observed_states_at_create == [ExecutionJobState.ANALYZING]
    assert store.get(job.id).state == ExecutionJobState.READY


def test_resolver_is_passed_through_to_destination_validation():
    """A collection with a variable host resolves via the injected resolver
    instead of making real DNS calls.
    """
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    collection = pm_collection("Demo", [pm_request("Ping", "GET", "https://internal.example.test/x")])
    runner = FakeNewmanRunner([
        RunOutcome(success=True, report_bytes=_newman_report("tok_AAA111")),
        RunOutcome(success=True, report_bytes=_newman_report("tok_ZZZ999")),
    ])

    def fake_resolver(hostname: str) -> list[str]:
        assert hostname == "internal.example.test"
        return ["93.184.216.34"]

    _run(job, runner, store, collection_data=collection, resolver=fake_resolver)
    final = store.get(job.id)
    assert final.state == ExecutionJobState.READY


# --- Fix round 1: any unexpected exception in run_job must still end terminal ---


def test_resolver_exception_during_validation_marks_failed_generically():
    """A resolver raising something other than ProblemException (e.g. a raw
    OSError from a real DNS lookup) must not leave the job stuck VALIDATING.
    """
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    collection = pm_collection("Demo", [pm_request("Ping", "GET", "https://internal.example.test/x")])
    runner = FakeNewmanRunner([])

    def raising_resolver(hostname: str) -> list[str]:
        raise OSError("dns server unreachable at 10.0.0.5")

    _run(job, runner, store, collection_data=collection, resolver=raising_resolver)
    final = store.get(job.id)
    assert final.state == ExecutionJobState.FAILED
    assert final.error_code == "unexpected_error"
    assert final.stage_history[-1] == "failed"
    assert "dns server" not in (final.error_detail or "")
    assert "10.0.0.5" not in (final.error_detail or "")
    assert runner.calls == []


def test_analysis_store_create_exception_marks_failed_generically():
    """analysis_store.create raising must not leave the job stuck ANALYZING."""
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    analysis_store = InMemorySessionStore(ttl_seconds=60)

    def raising_create(analysis) -> None:
        raise RuntimeError("disk full writing /var/data/sessions token=abc123")

    analysis_store.create = raising_create  # type: ignore[method-assign]
    job = _queued_job(store)
    runner = FakeNewmanRunner([
        RunOutcome(success=True, report_bytes=_newman_report("tok_AAA111")),
        RunOutcome(success=True, report_bytes=_newman_report("tok_ZZZ999")),
    ])
    _run(job, runner, store, analysis_store=analysis_store)
    final = store.get(job.id)
    assert final.state == ExecutionJobState.FAILED
    assert final.error_code == "unexpected_error"
    assert final.stage_history[-1] == "failed"
    assert final.analysis_id is None
    assert "disk full" not in (final.error_detail or "")
    assert "abc123" not in (final.error_detail or "")


# --- Fix round 1: destination-validation warnings must not leak variable values ---


def test_destination_validation_warnings_redact_variable_values():
    """A warning that echoes a supplied/resolved variable value (e.g. a
    blocked hostname) must have that value replaced with its `{{name}}`
    placeholder before it ever lands on job.warnings - job records must never
    carry a raw variable value.
    """
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    collection = pm_collection("Demo", [pm_request("Ping", "GET", "https://{{host}}/x")])
    runner = FakeNewmanRunner([])
    _run(job, runner, store, collection_data=collection, variable_values={"host": "localhost"})
    final = store.get(job.id)
    assert final.state == ExecutionJobState.FAILED
    assert final.error_code == "destination_validation_failed"
    assert not any("localhost" in w for w in final.warnings)
    assert any("{{host}}" in w for w in final.warnings)


# --- F3: run isolation and runner input ---


def _dict_ids(obj) -> set[int]:
    """ids of every dict/list reachable from `obj` (a RunInput's mutable parts)."""
    found: set[int] = set()
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            found.add(id(cur))
            stack.extend(cur.values())
        elif isinstance(cur, list):
            found.add(id(cur))
            stack.extend(cur)
    return found


def _run_input_ids(run_input: RunInput) -> set[int]:
    return (
        _dict_ids(run_input.collection_data)
        | _dict_ids(run_input.environment_data)
        | _dict_ids(run_input.supplied_values)
    )


class _MutatingRunner:
    """Mutates everything it is handed on run 1, then records a snapshot of what
    it receives on run 2 - so leakage between runs is directly observable.
    """

    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[RunInput] = []
        self.second_run_snapshot: dict | None = None

    def run(self, run_input: RunInput, **_kwargs) -> RunOutcome:
        self.calls.append(run_input)
        if len(self.calls) == 1:
            run_input.collection_data["info"]["name"] = "MUTATED"
            run_input.collection_data["item"].append({"name": "injected"})
            assert run_input.environment_data is not None
            run_input.environment_data["values"].append({"key": "evil", "value": "x"})
            run_input.supplied_values["token"] = "MUTATED"
        else:
            self.second_run_snapshot = json.loads(json.dumps({
                "collection": run_input.collection_data,
                "environment": run_input.environment_data,
                "supplied": run_input.supplied_values,
            }))
        return self.outcomes.pop(0)


def test_each_run_gets_a_fresh_deep_copy_of_its_input():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    collection = {"info": {"name": "Demo"}, "item": []}
    environment = {"name": "Env", "values": [{"key": "a", "value": "b"}]}
    supplied = {"token": "tok_original"}
    original = json.loads(json.dumps({"collection": collection, "environment": environment, "supplied": supplied}))
    runner = _MutatingRunner([
        RunOutcome(success=True, report_bytes=_newman_report("tok_AAA111")),
        RunOutcome(success=True, report_bytes=_newman_report("tok_ZZZ999")),
    ])
    _run(job, runner, store, collection_data=collection, environment_data=environment, supplied_values=supplied)

    assert store.get(job.id).state == ExecutionJobState.READY
    assert len(runner.calls) == 2
    # Run A's mutations never reach Run B ...
    assert runner.second_run_snapshot == original
    # ... because the two runs share no dict/list objects at all ...
    assert _run_input_ids(runner.calls[0]).isdisjoint(_run_input_ids(runner.calls[1]))
    # ... nor with the caller's own material.
    caller_ids = _dict_ids(collection) | _dict_ids(environment) | _dict_ids(supplied)
    assert _run_input_ids(runner.calls[0]).isdisjoint(caller_ids)
    assert _run_input_ids(runner.calls[1]).isdisjoint(caller_ids)
    assert {"collection": collection, "environment": environment, "supplied": supplied} == original


def test_run_input_carries_only_the_supplied_values():
    """Newman resolves collection/environment variables natively from
    collection_data/environment_data; the merged map is for destination
    validation and redaction only and never reaches the runner.
    """
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    runner = FakeNewmanRunner([
        RunOutcome(success=True, report_bytes=_newman_report("tok_AAA111")),
        RunOutcome(success=True, report_bytes=_newman_report("tok_ZZZ999")),
    ])
    _run(
        job, runner, store,
        variable_values={"host": "93.184.216.34", "env_only": "e-value", "token": "supplied-token"},
        supplied_values={"token": "supplied-token"},
    )
    assert store.get(job.id).state == ExecutionJobState.READY
    assert [c.supplied_values for c in runner.calls] == [{"token": "supplied-token"}] * 2
    assert not hasattr(runner.calls[0], "variable_values")


# --- F4: cancellation reaches the runner; the cap counts live workers ---


def _cancel_like_delete_endpoint(store, job_id) -> None:
    def _cancel(j: ExecutionJob) -> None:
        j.cancel_requested = True
        j.state = ExecutionJobState.CANCELLED
        j.stage_history.append("cancelled")

    store.mutate(job_id, _cancel)


class _ProbeRunner:
    """Records should_cancel() before/after a mid-run cancel, and the job's
    worker_active flag plus the owner's active count as seen from inside run().
    """

    def __init__(self, store, job_id, *, cancel_mid_run: bool, outcomes) -> None:
        self.store = store
        self.job_id = job_id
        self.cancel_mid_run = cancel_mid_run
        self.outcomes = list(outcomes)
        self.calls: list[RunInput] = []
        self.observed: list[dict] = []

    def run(self, run_input: RunInput, *, should_cancel) -> RunOutcome:
        self.calls.append(run_input)
        seen = {"before": should_cancel(), "worker_active": self.store.get(self.job_id).worker_active}
        if self.cancel_mid_run:
            _cancel_like_delete_endpoint(self.store, self.job_id)
            seen["count_active_after_cancel"] = self.store.count_active("127.0.0.1")
            try:
                self.store.create_if_allowed(
                    ExecutionJob(id="resubmit", owner_key="127.0.0.1", collection_name="d",
                                 created_at=time.time(), expires_at=time.time() + 60),
                    window_seconds=600, max_active=1,
                )
                seen["resubmit"] = "created"
            except Exception as exc:  # noqa: BLE001 - recorded for assertion
                seen["resubmit"] = getattr(exc, "status", type(exc).__name__)
        seen["after"] = should_cancel()
        self.observed.append(seen)
        return self.outcomes.pop(0)


def test_should_cancel_reflects_a_cancel_requested_mid_run():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    runner = _ProbeRunner(store, job.id, cancel_mid_run=True,
                          outcomes=[RunOutcome(success=True, report_bytes=_newman_report("tok_AAA111"))])
    _run(job, runner, store)
    assert runner.observed[0]["before"] is False
    assert runner.observed[0]["after"] is True
    assert store.get(job.id).state == ExecutionJobState.CANCELLED
    assert len(runner.calls) == 1


def test_should_cancel_is_true_when_the_job_is_deleted_mid_run():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    results: list[bool] = []

    class _DeleteThenProbe:
        def run(self, run_input: RunInput, *, should_cancel) -> RunOutcome:
            store.delete(job.id)
            results.append(should_cancel())
            return RunOutcome(success=True, report_bytes=_newman_report("tok"))

    _run(job, _DeleteThenProbe(), store)
    assert results == [True]
    assert store.get(job.id) is None  # finally-clear tolerated the missing job; nothing resurrected


def test_worker_active_is_set_during_the_run_and_cleared_after():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    runner = _ProbeRunner(store, job.id, cancel_mid_run=False, outcomes=[
        RunOutcome(success=True, report_bytes=_newman_report("tok_AAA111")),
        RunOutcome(success=True, report_bytes=_newman_report("tok_ZZZ999")),
    ])
    _run(job, runner, store)
    assert [o["worker_active"] for o in runner.observed] == [True, True]
    final = store.get(job.id)
    assert final.state == ExecutionJobState.READY
    assert final.worker_active is False


def test_worker_active_is_cleared_even_after_an_unexpected_exception():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    collection = pm_collection("Demo", [pm_request("Ping", "GET", "https://internal.example.test/x")])

    def raising_resolver(hostname: str) -> list[str]:
        raise OSError("boom")

    _run(job, FakeNewmanRunner([]), store, collection_data=collection, resolver=raising_resolver)
    final = store.get(job.id)
    assert final.state == ExecutionJobState.FAILED
    assert final.worker_active is False


def test_cancel_and_resubmit_cannot_exceed_the_cap_while_the_worker_runs():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    runner = _ProbeRunner(store, job.id, cancel_mid_run=True,
                          outcomes=[RunOutcome(success=True, report_bytes=_newman_report("tok_AAA111"))])
    _run(job, runner, store)
    seen = runner.observed[0]
    assert seen["count_active_after_cancel"] == 1  # cancelled, but its worker is still live
    assert seen["resubmit"] == 429
    assert store.get("resubmit") is None
    assert store.count_active("127.0.0.1") == 0  # worker finished -> slot released


def test_job_already_claimed_by_a_live_worker_is_not_run_twice():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    job.worker_active = True
    store.update(job)
    runner = FakeNewmanRunner([])
    _run(job, runner, store)
    assert runner.calls == []
    final = store.get(job.id)
    assert final.state == ExecutionJobState.QUEUED
    assert final.worker_active is True  # the other worker's claim is left untouched


# --- F5: case-insensitive redaction; swallowed exceptions logged without messages ---


def test_redaction_is_case_insensitive():
    """A mixed-case supplied host is normalized (lower-cased) by URL parsing, so
    the warning echoes it in a different case - it must still be redacted.
    """
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    collection = pm_collection("Demo", [pm_request("Ping", "GET", "https://{{host}}/x")])
    _run(job, FakeNewmanRunner([]), store, collection_data=collection, variable_values={"host": "LocalHost"})
    final = store.get(job.id)
    assert final.error_code == "destination_validation_failed"
    assert final.warnings
    assert not any("localhost" in w.lower() for w in final.warnings)
    assert any("{{host}}" in w for w in final.warnings)


def test_redact_variable_values_replaces_every_case_variant():
    text = "Blocked SECRET-host.example and secret-HOST.example"
    assert _redact_variable_values(text, {"h": "secret-host.example"}) == "Blocked {{h}} and {{h}}"


_SERVICE_LOGGER = "execution_job_service"


def _swallow_records(caplog) -> list:
    return [r for r in caplog.records if r.name == _SERVICE_LOGGER and getattr(r, "exc_type", None)]


def _assert_no_leak(records, *secrets: str) -> None:
    for r in records:
        assert r.exc_info is None  # a traceback would carry the message
        rendered = r.getMessage() + " " + " ".join(str(v) for v in vars(r).values())
        for secret in secrets:
            assert secret not in rendered


def test_runner_exception_is_logged_with_type_only(caplog):
    caplog.set_level(logging.WARNING, logger=_SERVICE_LOGGER)
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    _run(job, _RaisingRunner(), store)
    records = _swallow_records(caplog)
    assert [(r.job_id, r.stage, r.exc_type) for r in records] == [(job.id, "running_baseline", "RuntimeError")]
    _assert_no_leak(records, "secret_token", "abc123")


def test_unexpected_drive_exception_is_logged_with_type_only(caplog):
    caplog.set_level(logging.WARNING, logger=_SERVICE_LOGGER)
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    collection = pm_collection("Demo", [pm_request("Ping", "GET", "https://internal.example.test/x")])

    def raising_resolver(hostname: str) -> list[str]:
        raise OSError("dns server unreachable at 10.0.0.5")

    _run(job, FakeNewmanRunner([]), store, collection_data=collection, resolver=raising_resolver)
    records = _swallow_records(caplog)
    assert [(r.job_id, r.stage, r.exc_type) for r in records] == [(job.id, "validating", "OSError")]
    _assert_no_leak(records, "dns server", "10.0.0.5")


def test_unexpected_analysis_exception_is_logged_with_type_only(caplog, monkeypatch):
    caplog.set_level(logging.WARNING, logger=_SERVICE_LOGGER)
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    runner = FakeNewmanRunner([
        RunOutcome(success=True, report_bytes=_newman_report("tok_AAA111")),
        RunOutcome(success=True, report_bytes=_newman_report("tok_ZZZ999")),
    ])

    def _boom(files, settings):
        raise RuntimeError("db password hunter2 in traceback")

    monkeypatch.setattr(analysis_service, "build_analysis", _boom)
    _run(job, runner, store)
    records = _swallow_records(caplog)
    assert [(r.job_id, r.stage, r.exc_type) for r in records] == [(job.id, "analyzing", "RuntimeError")]
    _assert_no_leak(records, "hunter2")


def test_log_formatter_emits_job_id_and_exc_type():
    record = logging.LogRecord(_SERVICE_LOGGER, logging.WARNING, __file__, 1, "swallowed", None, None)
    record.job_id = "job_1"
    record.stage = "analyzing"
    record.exc_type = "RuntimeError"
    payload = json.loads(RedactingJsonFormatter().format(record))
    assert (payload["job_id"], payload["stage"], payload["exc_type"]) == ("job_1", "analyzing", "RuntimeError")


def test_runner_receives_the_validated_host_pins():
    store = InMemoryExecutionJobStore(ttl_seconds=60)
    job = _queued_job(store)
    runner = FakeNewmanRunner([
        RunOutcome(success=True, report_bytes=_newman_report("tok_AAA111")),
        RunOutcome(success=True, report_bytes=_newman_report("tok_ZZZ999")),
    ])
    collection = pm_collection("Demo", [pm_request("Ping", "GET", "https://api.example.com/ping")])
    _run(job, runner, store, collection_data=collection, resolver=lambda host: ["93.184.216.34"])
    assert store.get(job.id).state == ExecutionJobState.READY
    assert runner.calls[0].host_pins == {"api.example.com": ["93.184.216.34"]}
    assert runner.calls[1].host_pins == {"api.example.com": ["93.184.216.34"]}
    assert runner.calls[0].host_pins is not runner.calls[1].host_pins
