from app.core.config import Settings


def test_newman_runner_defaults():
    s = Settings()
    assert s.newman_runner == "disabled"
    assert s.newman_docker_image == "baseline11/newman:6.2.2"
    assert s.newman_version == "6.2.2"
    assert s.newman_container_memory == "512m"
    assert s.newman_container_cpus == "1.0"
    assert s.newman_container_pids_limit == 256
    assert s.newman_request_timeout_ms == 30_000
    assert s.newman_start_grace_seconds == 30
    assert s.docker_binary == "docker"
    assert s.max_concurrent_executions == 4


def test_docker_is_an_accepted_runner_mode():
    assert Settings(newman_runner="docker").newman_runner == "docker"
