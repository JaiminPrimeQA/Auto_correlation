from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.newman_command import build_docker_argv


def _argv(**overrides) -> list[str]:
    base = dict(settings=Settings(), container_name="b11-newman-abc123", workspace_root=Path("/tmp/b11-newman-x"),
                folder_name=None, host_pins={}, container_user=None, timeout_seconds=300)
    base.update(overrides)
    return build_docker_argv(**base)


def _pairs(argv: list[str]) -> list[tuple[str, str]]:
    return list(zip(argv, argv[1:], strict=False))


def test_isolation_and_limit_flags():
    argv = _argv()
    s = Settings()
    assert argv[:3] == ["docker", "run", "--rm"]
    for flag in ("--read-only", "--rm"):
        assert flag in argv
    pairs = _pairs(argv)
    assert ("--cap-drop", "ALL") in pairs
    assert ("--security-opt", "no-new-privileges") in pairs
    assert ("--pids-limit", str(s.newman_container_pids_limit)) in pairs
    assert ("--memory", s.newman_container_memory) in pairs
    assert ("--cpus", s.newman_container_cpus) in pairs
    assert ("--dns", "127.0.0.1") in pairs
    assert ("--network", "bridge") in pairs
    assert ("--name", "b11-newman-abc123") in pairs
    assert any(a == "--tmpfs" and b.startswith("/tmp:") for a, b in pairs)
    assert any(a == "--ulimit" and b.startswith("fsize=") for a, b in pairs)
    joined = " ".join(argv)
    for forbidden in ("--privileged", "docker.sock", "--network=host", "host-network"):
        assert forbidden not in joined
    flags = {arg.split("=", 1)[0] for arg in argv}
    assert not flags & {"--pid", "--ipc", "--uts", "--userns", "--cap-add", "--device"}


def test_workspace_is_the_only_mount():
    argv = _argv(workspace_root=Path("/tmp/b11-newman-x"))
    mounts = [b for a, b in _pairs(argv) if a in ("--mount", "-v", "--volume")]
    assert mounts == [f"type=bind,src={Path('/tmp/b11-newman-x')},dst=/job"]


def test_newman_arguments():
    argv = _argv(timeout_seconds=300)
    image_index = argv.index(Settings().newman_docker_image)
    newman = argv[image_index + 1:]
    assert newman[:2] == ["run", "/job/collection.json"]
    pairs = _pairs(newman)
    assert ("--environment", "/job/environment.json") in pairs
    assert ("--reporters", "json") in pairs
    assert ("--reporter-json-export", "/job/out/report.json") in pairs
    assert ("--timeout", "300000") in pairs
    assert ("--timeout-request", str(Settings().newman_request_timeout_ms)) in pairs
    assert ("--working-dir", "/job/files") in pairs
    for flag in ("--ignore-redirects", "--no-insecure-file-read", "--disable-unicode"):
        assert flag in newman
    assert "--insecure" not in newman
    assert not any(a.startswith("--folder") for a in newman)


def test_folder_is_passed_as_a_single_equals_argument():
    argv = _argv(folder_name="-rf Auth")
    assert "--folder=-rf Auth" in argv


def test_host_pins_become_sorted_add_host_flags():
    argv = _argv(host_pins={"b.example.com": ["93.184.216.35"], "a.example.com": ["93.184.216.34", "2001:db8::1"]})
    adds = [b for a, b in _pairs(argv) if a == "--add-host"]
    assert adds == ["a.example.com:2001:db8::1", "a.example.com:93.184.216.34", "b.example.com:93.184.216.35"]


@pytest.mark.parametrize("pins", [
    {"--privileged": ["93.184.216.34"]},
    {"a.example.com": ["not-an-ip"]},
    {"a b.example.com": ["93.184.216.34"]},
])
def test_invalid_pins_are_rejected(pins):
    with pytest.raises(ValueError):
        _argv(host_pins=pins)


def test_invalid_container_name_is_rejected():
    with pytest.raises(ValueError):
        _argv(container_name="--rm")


def test_workspace_path_with_a_comma_is_rejected():
    with pytest.raises(ValueError):
        _argv(workspace_root=Path("/tmp/a,dst=/etc"))


def test_container_user_is_applied_when_given():
    assert ("--user", "1000:1000") in _pairs(_argv(container_user="1000:1000"))
    assert "--user" not in _argv(container_user=None)
