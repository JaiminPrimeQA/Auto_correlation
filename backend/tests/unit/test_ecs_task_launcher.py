import boto3
import pytest
from botocore.stub import ANY, Stubber

from app.core.config import Settings
from app.services.ecs_newman_runner import EcsTaskLauncher, LaunchError

_SETTINGS = Settings(
    ecs_cluster="b11", runner_task_definition="b11-runner:3", runner_subnets="subnet-a,subnet-b",
    runner_security_groups="sg-1", runner_container_name="newman-runner",
)
_ARN = "arn:aws:ecs:eu-west-1:123456789012:task/b11/abc"


@pytest.fixture
def ecs():
    client = boto3.client("ecs", region_name="eu-west-1", aws_access_key_id="x", aws_secret_access_key="x")
    with Stubber(client) as stub:
        yield client, stub


def test_start_runs_one_fargate_task_without_a_public_ip(ecs):
    client, stub = ecs
    stub.add_response("run_task", {"tasks": [{"taskArn": _ARN}], "failures": []}, {
        "cluster": "b11",
        "taskDefinition": "b11-runner:3",
        "launchType": "FARGATE",
        "count": 1,
        "startedBy": "baseline11-worker",
        "referenceId": "b11-run_1",
        "networkConfiguration": {"awsvpcConfiguration": {
            "subnets": ["subnet-a", "subnet-b"], "securityGroups": ["sg-1"], "assignPublicIp": "DISABLED",
        }},
        "overrides": {"containerOverrides": [{
            "name": "newman-runner",
            "environment": [{"name": "RUN_INPUT_URL", "value": "https://in"}],
        }]},
    })
    assert EcsTaskLauncher(client=client, settings=_SETTINGS).start(
        env={"RUN_INPUT_URL": "https://in"}, name="b11-run_1") == _ARN


def test_start_failure_is_a_launch_error(ecs):
    client, stub = ecs
    stub.add_response("run_task", {"tasks": [], "failures": [{"reason": "RESOURCE:ENI"}]}, None)
    with pytest.raises(LaunchError):
        EcsTaskLauncher(client=client, settings=_SETTINGS).start(env={}, name="n")


def test_status_reads_the_runner_container_exit_code(ecs):
    client, stub = ecs
    stub.add_response("describe_tasks", {"tasks": [{"lastStatus": "RUNNING"}]}, {"cluster": "b11", "tasks": [_ARN]})
    stub.add_response("describe_tasks", {"tasks": [{
        "lastStatus": "STOPPED", "stopCode": "EssentialContainerExited", "stoppedReason": "Essential container exited",
        "containers": [{"name": "sidecar", "exitCode": 9}, {"name": "newman-runner", "exitCode": 1}],
    }]}, {"cluster": "b11", "tasks": [_ARN]})
    launcher = EcsTaskLauncher(client=client, settings=_SETTINGS)
    assert launcher.status(_ARN).stopped is False
    status = launcher.status(_ARN)
    assert (status.stopped, status.exit_code, status.stop_code) == (True, 1, "EssentialContainerExited")


def test_stop_tolerates_an_already_stopped_task(ecs):
    client, stub = ecs
    stub.add_client_error("stop_task", "InvalidParameterException", expected_params={
        "cluster": "b11", "task": _ARN, "reason": ANY})
    EcsTaskLauncher(client=client, settings=_SETTINGS).stop(_ARN)
