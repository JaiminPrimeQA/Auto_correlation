from app.core.config import Settings


def test_execution_job_limit_defaults():
    s = Settings()
    assert s.max_concurrent_jobs_per_owner == 2
    assert s.job_ttl_seconds == 30 * 60
    assert s.job_run_timeout_seconds == 5 * 60
    assert s.max_report_bytes == 25 * 1024 * 1024
    assert s.idempotency_window_seconds == 10 * 60
