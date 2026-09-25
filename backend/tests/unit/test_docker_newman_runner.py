import json
import subprocess
from pathlib import Path

from app.core.config import Settings
from app.domain.execution_job import RunInput
from app.services.docker_newman_runner import DockerNewmanRunner
from tests.fixtures.postman_builders import pm_collection, pm_folder, pm_request

_REPORT = json.dumps({"collection": {"info": {"name": "C"}}, "run": {"executions": []}}).encode()


def _mount_source(argv: list[str]) -> Path:
    spec = argv[argv.index("--mount") + 1]
    fields = dict(part.split("=", 1) for part in spec.split(","))
    return Path(fields["src"])


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class _Proc:
    def __init__(self, docker):
        self._docker = docker
        self._polls = 0
        self.returncode = None

    def poll(self):
        if self._docker.never_exits and not self._docker.killed:
            return None
        if self._docker.killed:
            self.returncode = 137
            return 137
        self._polls += 1
        if self._polls > self._docker.polls_before_exit:
            self.returncode = self._docker.exit_code
            return self.returncode
        return None

    def wait(self, timeout=None):
        return self.poll() if self.poll() is not None else 137

    def kill(self):
        self._docker.killed.append("proc")


class _FakeDocker:
    def __init__(self, *, exit_code=0, report=_REPORT, polls_before_exit=0, never_exits=False, popen_error=None):
        self.exit_code, self.report = exit_code, report
        self.polls_before_exit, self.never_exits = polls_before_exit, never_exits
        self.popen_error = popen_error
        self.argv: list[str] | None = None
        self.kwargs: dict = {}
        self.killed: list[str] = []
        self.workspace: Path | None = None

    def popen(self, argv, **kwargs):
        if self.popen_error:
            raise self.popen_error
        self.argv, self.kwargs = argv, kwargs
        self.workspace = _mount_source(argv)
        if self.report is not None:
            (self.workspace / "out" / "report.json").write_bytes(self.report)
        return _Proc(self)

    def kill_container(self, name: str) -> None:
        self.killed.append(name)


def _runner(docker: _FakeDocker, tmp_path: Path, clock: _Clock | None = None) -> DockerNewmanRunner:
    clock = clock or _Clock()
    return DockerNewmanRunner(
        Settings(), popen=docker.popen, kill_container=docker.kill_container, workspace_base=tmp_path,
        poll_interval=0.5, clock=clock, sleep=clock.sleep, container_user=None,
    )


def _input(**overrides) -> RunInput:
    base = dict(collection_data=pm_collection("C", [pm_request("R", "GET", "https://93.184.216.34/")]),
                environment_data=None, supplied_values={"token": "s3cret-value"}, folder_id=None,
                timeout_seconds=300, host_pins={"api.example.com": ["93.184.216.34"]})
    base.update(overrides)
    return RunInput(**base)


def _never() -> bool:
    return False


def test_successful_run_returns_the_report_and_cleans_up(tmp_path):
    docker = _FakeDocker(exit_code=0)
    outcome = _runner(docker, tmp_path).run(_input(), should_cancel=_never)
    assert outcome.success is True
    assert outcome.report_bytes == _REPORT
    assert docker.workspace is not None and not docker.workspace.exists()


def test_assertion_failures_exit_1_still_yield_the_report(tmp_path):
    outcome = _runner(_FakeDocker(exit_code=1), tmp_path).run(_input(), should_cancel=_never)
    assert outcome.success is True


def test_exit_1_without_a_report_is_missing_report(tmp_path):
    outcome = _runner(_FakeDocker(exit_code=1, report=None), tmp_path).run(_input(), should_cancel=_never)
    assert (outcome.success, outcome.error_code) == (False, "missing_report")


def test_process_is_started_without_a_shell_and_without_output_capture(tmp_path):
    docker = _FakeDocker()
    _runner(docker, tmp_path).run(_input(), should_cancel=_never)
    assert isinstance(docker.argv, list)
    assert docker.kwargs.get("shell", False) is False
    assert docker.kwargs["stdout"] is subprocess.DEVNULL
    assert docker.kwargs["stderr"] is subprocess.DEVNULL
    assert docker.kwargs["stdin"] is subprocess.DEVNULL


def test_supplied_values_never_appear_in_argv(tmp_path):
    docker = _FakeDocker()
    _runner(docker, tmp_path).run(_input(), should_cancel=_never)
    assert "s3cret-value" not in " ".join(docker.argv or [])


def test_host_pins_reach_the_container(tmp_path):
    docker = _FakeDocker()
    _runner(docker, tmp_path).run(_input(), should_cancel=_never)
    argv = docker.argv or []
    assert argv[argv.index("--add-host") + 1] == "api.example.com:93.184.216.34"


def test_invalid_host_pin_fails_without_starting_docker(tmp_path):
    docker = _FakeDocker()
    outcome = _runner(docker, tmp_path).run(_input(host_pins={"api.example.com": ["nope"]}), should_cancel=_never)
    assert (outcome.success, outcome.error_code) == (False, "invalid_destination")
    assert docker.argv is None


def test_folder_id_is_translated_to_a_folder_name(tmp_path):
    from app.services.postman_folder_extractor import extract_folders
    collection = pm_collection("C", [pm_folder("Auth", [pm_request("L", "POST", "https://93.184.216.34/")])])
    docker = _FakeDocker()
    _runner(docker, tmp_path).run(
        _input(collection_data=collection, folder_id=extract_folders(collection)[0].id), should_cancel=_never,
    )
    assert "--folder=Auth" in (docker.argv or [])


def test_unknown_folder_fails_without_starting_docker(tmp_path):
    docker = _FakeDocker()
    outcome = _runner(docker, tmp_path).run(_input(folder_id="nope"), should_cancel=_never)
    assert (outcome.success, outcome.error_code) == (False, "invalid_folder")
    assert docker.argv is None


def test_docker_missing_is_runner_unavailable(tmp_path):
    docker = _FakeDocker(popen_error=FileNotFoundError("docker"))
    outcome = _runner(docker, tmp_path).run(_input(), should_cancel=_never)
    assert (outcome.success, outcome.error_code) == (False, "runner_unavailable")


def test_docker_exit_125_is_runner_unavailable(tmp_path):
    outcome = _runner(_FakeDocker(exit_code=125, report=None), tmp_path).run(_input(), should_cancel=_never)
    assert outcome.error_code == "runner_unavailable"


def test_exit_137_is_resource_limit(tmp_path):
    outcome = _runner(_FakeDocker(exit_code=137, report=None), tmp_path).run(_input(), should_cancel=_never)
    assert outcome.error_code == "resource_limit"


def test_other_exit_codes_are_process_failed(tmp_path):
    outcome = _runner(_FakeDocker(exit_code=3, report=None), tmp_path).run(_input(), should_cancel=_never)
    assert outcome.error_code == "process_failed"


def test_wall_clock_timeout_kills_the_container(tmp_path):
    docker = _FakeDocker(never_exits=True, report=None)
    outcome = _runner(docker, tmp_path).run(_input(timeout_seconds=5), should_cancel=_never)
    assert (outcome.success, outcome.error_code) == (False, "timeout")
    assert any(name.startswith("b11-newman-") for name in docker.killed)
    assert docker.workspace is not None and not docker.workspace.exists()


def test_cancellation_kills_the_container(tmp_path):
    docker = _FakeDocker(never_exits=True, report=None)
    calls = {"n": 0}

    def cancel_on_third_check() -> bool:
        calls["n"] += 1
        return calls["n"] >= 3

    outcome = _runner(docker, tmp_path).run(_input(), should_cancel=cancel_on_third_check)
    assert (outcome.success, outcome.error_code) == (False, "cancelled")
    assert any(name.startswith("b11-newman-") for name in docker.killed)


def test_each_run_uses_a_fresh_container_name_and_workspace(tmp_path):
    docker = _FakeDocker()
    runner = _runner(docker, tmp_path)
    runner.run(_input(), should_cancel=_never)
    first = (docker.argv[docker.argv.index("--name") + 1], docker.workspace)
    runner.run(_input(), should_cancel=_never)
    second = (docker.argv[docker.argv.index("--name") + 1], docker.workspace)
    assert first[0] != second[0]
    assert first[1] != second[1]


def test_error_details_are_fixed_and_contain_no_values(tmp_path):
    for exit_code in (3, 125, 137):
        outcome = _runner(_FakeDocker(exit_code=exit_code, report=None), tmp_path).run(_input(), should_cancel=_never)
        assert outcome.error_detail
        assert "s3cret-value" not in outcome.error_detail
