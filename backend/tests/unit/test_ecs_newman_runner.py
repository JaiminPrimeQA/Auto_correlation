import json
from urllib.parse import urlparse

import boto3
import pytest

from app.core.config import Settings
from app.domain.execution_job import RunInput
from app.repositories.s3_object_store import S3ObjectStore
from app.services.ecs_newman_runner import EcsTaskNewmanRunner, LaunchError, TaskStatus
from tests.fixtures.aws import BUCKET, REGION, aws_mocks
from tests.fixtures.postman_builders import pm_collection, pm_folder, pm_request

_REPORT = json.dumps({"collection": {"info": {"name": "C"}}, "run": {"executions": []}}).encode()


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _key(url: str) -> str:
    """Object key from a presigned URL (virtual-hosted or path style)."""
    path = urlparse(url).path.lstrip("/")
    return path[len(BUCKET) + 1:] if path.startswith(f"{BUCKET}/") else path


class FakeLauncher:
    """Plays the Fargate task: reads its input object, 'runs', and writes the
    report object the way the real task's presigned PUT would."""

    def __init__(self, objects, *, exit_code=0, report=_REPORT, polls=1, never_stops=False,
                 stop_code="EssentialContainerExited", reason=None, start_error=None):
        self.objects = objects
        self.exit_code, self.report, self.polls = exit_code, report, polls
        self.never_stops, self.stop_code, self.reason = never_stops, stop_code, reason
        self.start_error = start_error
        self.started: list[dict] = []
        self.stopped: list[str] = []
        self.inputs: list[dict] = []
        self._polls = 0

    def start(self, *, env, name):
        if self.start_error:
            raise self.start_error
        self.started.append({"env": env, "name": name})
        self.inputs.append(self.objects.get_json(_key(env["RUN_INPUT_URL"]), max_bytes=10_000_000))
        self._report_key = _key(env["REPORT_UPLOAD_URL"])
        return f"task-{len(self.started)}"

    def status(self, handle):
        if self.stopped:
            return TaskStatus(stopped=True, exit_code=None, stop_code="UserInitiated", reason="stopped")
        if self.never_stops:
            return TaskStatus(stopped=False)
        self._polls += 1
        if self._polls < self.polls:
            return TaskStatus(stopped=False)
        if self.report is not None:
            self.objects.put_bytes(self._report_key, self.report)
        return TaskStatus(stopped=True, exit_code=self.exit_code, stop_code=self.stop_code, reason=self.reason)

    def stop(self, handle):
        self.stopped.append(handle)


@pytest.fixture
def objects():
    with aws_mocks():
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
        yield S3ObjectStore(client=s3, bucket=BUCKET, kms_key_id=None), s3


def _runner(objects, launcher, clock=None):
    clock = clock or _Clock()
    return EcsTaskNewmanRunner(Settings(), objects=objects, launcher=launcher, clock=clock, sleep=clock.sleep,
                               poll_interval=5)


def _input(**overrides):
    base = dict(collection_data=pm_collection("C", [pm_request("R", "GET", "https://api.example.com/")]),
                environment_data={"name": "dev", "values": [{"key": "token", "value": "", "enabled": False}]},
                supplied_values={"token": "s3cret-value"}, folder_id=None, timeout_seconds=300,
                host_pins={}, job_id="job_1")
    base.update(overrides)
    return RunInput(**base)


def _never():
    return False


def _remaining(s3):
    return [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET).get("Contents", [])]


def test_successful_task_returns_the_report_and_cleans_up(objects):
    store, s3 = objects
    launcher = FakeLauncher(store)
    outcome = _runner(store, launcher).run(_input(), should_cancel=_never)
    assert outcome.success is True
    assert outcome.report_bytes == _REPORT
    assert _remaining(s3) == []


def test_task_input_carries_values_in_the_object_never_in_env(objects):
    store, _ = objects
    launcher = FakeLauncher(store)
    _runner(store, launcher).run(_input(), should_cancel=_never)
    env = launcher.started[0]["env"]
    assert set(env) == {"RUN_INPUT_URL", "REPORT_UPLOAD_URL", "MAX_REPORT_BYTES"}
    assert "s3cret-value" not in json.dumps(launcher.started)
    task_input = launcher.inputs[0]
    token = {v["key"]: v for v in task_input["environment"]["values"]}["token"]
    assert token == {"key": "token", "value": "s3cret-value", "enabled": True}
    assert task_input["args"][:2] == ["run", "/tmp/job/collection.json"]
    assert "--ignore-redirects" in task_input["args"]


def test_folder_is_passed_by_name(objects):
    from app.services.postman_folder_extractor import extract_folders

    store, _ = objects
    collection = pm_collection("C", [pm_folder("Auth", [pm_request("L", "POST", "https://api.example.com/")])])
    launcher = FakeLauncher(store)
    _runner(store, launcher).run(_input(collection_data=collection, folder_id=extract_folders(collection)[0].id),
                                 should_cancel=_never)
    assert "--folder=Auth" in launcher.inputs[0]["args"]


def test_each_run_uses_its_own_objects(objects):
    store, _ = objects
    launcher = FakeLauncher(store)
    runner = _runner(store, launcher)
    runner.run(_input(), should_cancel=_never)
    runner.run(_input(), should_cancel=_never)
    first, second = (_key(s["env"]["RUN_INPUT_URL"]) for s in launcher.started)
    assert first != second
    assert first.startswith("jobs/job_1/runs/")


def test_assertion_failures_still_return_the_report(objects):
    store, _ = objects
    assert _runner(store, FakeLauncher(store, exit_code=1)).run(_input(), should_cancel=_never).success is True


@pytest.mark.parametrize("launcher_kwargs,code", [
    (dict(exit_code=0, report=None), "missing_report"),
    (dict(exit_code=0, report=b"{not json"), "malformed_report"),
    (dict(exit_code=72, report=None), "report_too_large"),
    (dict(exit_code=70, report=None), "runner_unavailable"),
    (dict(exit_code=71, report=None), "runner_unavailable"),
    (dict(exit_code=137, report=None), "resource_limit"),
    (dict(exit_code=None, report=None, reason="OutOfMemoryError: Container killed"), "resource_limit"),
    (dict(exit_code=None, report=None, stop_code="TaskFailedToStart", reason="CannotPullContainerError"),
     "runner_unavailable"),
    (dict(exit_code=3, report=None), "process_failed"),
])
def test_task_outcomes_map_to_stable_codes(objects, launcher_kwargs, code):
    store, s3 = objects
    outcome = _runner(store, FakeLauncher(store, **launcher_kwargs)).run(_input(), should_cancel=_never)
    assert (outcome.success, outcome.error_code) == (False, code)
    assert "s3cret-value" not in (outcome.error_detail or "")
    assert "CannotPull" not in (outcome.error_detail or "")
    assert _remaining(s3) == []


def test_oversized_uploaded_report_is_rejected(objects):
    store, _ = objects
    settings = Settings(max_report_bytes=10)
    clock = _Clock()
    runner = EcsTaskNewmanRunner(settings, objects=store, launcher=FakeLauncher(store), clock=clock,
                                 sleep=clock.sleep, poll_interval=5)
    assert runner.run(_input(), should_cancel=_never).error_code == "report_too_large"


def test_launch_failure_is_runner_unavailable(objects):
    store, s3 = objects
    launcher = FakeLauncher(store, start_error=LaunchError("RESOURCE:ENI"))
    outcome = _runner(store, launcher).run(_input(), should_cancel=_never)
    assert (outcome.success, outcome.error_code) == (False, "runner_unavailable")
    assert _remaining(s3) == []


def test_deadline_stops_the_task(objects):
    store, s3 = objects
    launcher = FakeLauncher(store, never_stops=True, report=None)
    outcome = _runner(store, launcher).run(_input(timeout_seconds=10), should_cancel=_never)
    assert (outcome.success, outcome.error_code) == (False, "timeout")
    assert launcher.stopped == ["task-1"]
    assert _remaining(s3) == []


def test_cancellation_stops_the_task(objects):
    store, _ = objects
    launcher = FakeLauncher(store, never_stops=True, report=None)
    checks = {"n": 0}

    def cancel_on_second_check():
        checks["n"] += 1
        return checks["n"] >= 2

    outcome = _runner(store, launcher).run(_input(), should_cancel=cancel_on_second_check)
    assert (outcome.success, outcome.error_code) == (False, "cancelled")
    assert launcher.stopped == ["task-1"]


def test_unknown_folder_never_launches(objects):
    store, _ = objects
    launcher = FakeLauncher(store)
    outcome = _runner(store, launcher).run(_input(folder_id="nope"), should_cancel=_never)
    assert outcome.error_code == "invalid_folder"
    assert launcher.started == []
