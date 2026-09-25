import json

import pytest

from app.core import metrics


@pytest.fixture
def lines(monkeypatch):
    captured: list[str] = []
    monkeypatch.setattr(metrics, "_write", captured.append)
    return captured


def test_emits_cloudwatch_embedded_metric_format(lines):
    metrics.emit("JobsCompleted", 1, unit="Count", outcome="failed", error_code="timeout")
    doc = json.loads(lines[0])
    directive = doc["_aws"]["CloudWatchMetrics"][0]
    assert directive["Namespace"] == "Baseline11/Execution"
    assert directive["Dimensions"] == [["outcome", "error_code"]]
    assert directive["Metrics"] == [{"Name": "JobsCompleted", "Unit": "Count"}]
    assert (doc["outcome"], doc["error_code"], doc["JobsCompleted"]) == ("failed", "timeout", 1)
    assert isinstance(doc["_aws"]["Timestamp"], int)


def test_dimension_values_must_be_plain_identifiers(lines):
    metrics.emit("JobsCompleted", 1, unit="Count", outcome="failed", error_code="Bearer s3cret token!")
    doc = json.loads(lines[0])
    assert doc["error_code"] == "other"
    assert "s3cret" not in lines[0]


def test_unknown_units_are_rejected():
    with pytest.raises(ValueError):
        metrics.emit("X", 1, unit="Parsecs")


def test_emit_never_raises_on_write_failure(monkeypatch):
    def _boom(_line):
        raise OSError("stdout closed")

    monkeypatch.setattr(metrics, "_write", _boom)
    metrics.emit("JobsCreated", 1, unit="Count")


def test_job_lifecycle_emits_created_completed_and_durations(lines):
    import time

    from app.core.config import Settings
    from app.domain.execution_job import ExecutionJob, RunOutcome
    from app.repositories.analysis_store import InMemorySessionStore
    from app.repositories.execution_job_store import InMemoryExecutionJobStore
    from app.services.execution_job_service import run_job
    from app.services.fake_newman_runner import FakeNewmanRunner

    store = InMemoryExecutionJobStore(ttl_seconds=60)
    now = time.time()
    store.create(ExecutionJob(id="j1", owner_key="o", collection_name="C", created_at=now, expires_at=now + 60))
    run_job("j1", collection_data={"info": {"name": "C"}, "item": []}, environment_data=None, variable_values={},
            supplied_values={}, folder_id=None,
            runner=FakeNewmanRunner([RunOutcome(False, error_code="timeout", error_detail="t")]),
            store=store, analysis_store=InMemorySessionStore(ttl_seconds=60), settings=Settings())
    docs = [json.loads(line) for line in lines]
    names = [d["_aws"]["CloudWatchMetrics"][0]["Metrics"][0]["Name"] for d in docs]
    assert "RunDurationSeconds" in names
    completed = next(d for d in docs if "JobsCompleted" in d)
    assert (completed["outcome"], completed["error_code"]) == ("failed", "timeout")


def test_completion_is_counted_once_per_job(lines):
    import time

    from app.domain.execution_job import ExecutionJob, ExecutionJobState
    from app.repositories.execution_job_store import InMemoryExecutionJobStore
    from app.services.execution_job_service import fail_job

    store = InMemoryExecutionJobStore(ttl_seconds=60)
    now = time.time()
    store.create(ExecutionJob(id="j1", owner_key="o", collection_name="C", created_at=now, expires_at=now + 60,
                              state=ExecutionJobState.RUNNING_BASELINE))
    fail_job("j1", store, "timeout", "t")
    fail_job("j1", store, "timeout", "t")  # already terminal: no second completion
    assert sum(1 for line in lines if "JobsCompleted" in line) == 1
