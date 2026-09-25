import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_execution_job_limit_defaults():
    s = Settings()
    assert s.max_concurrent_jobs_per_owner == 2
    assert s.job_ttl_seconds == 30 * 60
    assert s.job_run_timeout_seconds == 5 * 60
    assert s.max_report_bytes == 25 * 1024 * 1024
    assert s.idempotency_window_seconds == 10 * 60


def test_newman_runner_is_disabled_by_default():
    assert Settings().newman_runner == "disabled"


def test_newman_runner_accepts_fake_and_rejects_unknown_values():
    assert Settings(newman_runner="fake").newman_runner == "fake"
    with pytest.raises(ValidationError):
        Settings(newman_runner="kubernetes")
