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
              supplied_values={"host": "api.example.com"}, folder_id=None, timeout_seconds=300)
    RunOutcome(success=True, report_bytes=b'{"run": {}}', error_code=None, error_detail=None)
