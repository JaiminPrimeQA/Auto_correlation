from app.api import deps
from app.core.config import Settings
from app.services.docker_newman_runner import DockerNewmanRunner
from app.services.fake_newman_runner import FakeNewmanRunner


def test_docker_mode_returns_a_docker_runner_when_the_cli_exists(monkeypatch):
    monkeypatch.setattr(deps.shutil, "which", lambda name: "/usr/bin/docker")
    assert isinstance(deps.get_newman_runner(Settings(newman_runner="docker")), DockerNewmanRunner)


def test_docker_mode_without_the_cli_is_unavailable(monkeypatch):
    monkeypatch.setattr(deps.shutil, "which", lambda name: None)
    assert deps.get_newman_runner(Settings(newman_runner="docker")) is None


def test_fake_and_disabled_modes_are_unchanged():
    assert isinstance(deps.get_newman_runner(Settings(newman_runner="fake")), FakeNewmanRunner)
    assert deps.get_newman_runner(Settings(newman_runner="disabled")) is None
