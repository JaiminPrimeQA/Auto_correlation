import json
import stat
import sys

import pytest

from app.domain.execution_job import RunInput
from app.services.newman_workspace import build_environment, newman_workspace


def _run_input(**overrides) -> RunInput:
    base = dict(collection_data={"info": {"name": "C"}, "item": []}, environment_data=None,
                supplied_values={}, folder_id=None, timeout_seconds=300)
    base.update(overrides)
    return RunInput(**base)


def test_supplied_values_override_and_enable_environment_entries():
    env = {"name": "dev", "values": [
        {"key": "host", "value": "old.example.com", "enabled": True},
        {"key": "token", "value": "", "enabled": False},
        {"key": "keep", "value": "k", "enabled": True},
    ]}
    merged = build_environment(env, {"token": "s3cret", "extra": "e"})
    by_key = {v["key"]: v for v in merged["values"]}
    assert merged["name"] == "dev"
    assert by_key["token"] == {"key": "token", "value": "s3cret", "enabled": True}
    assert by_key["keep"]["value"] == "k"
    assert by_key["host"]["value"] == "old.example.com"
    assert by_key["extra"] == {"key": "extra", "value": "e", "enabled": True, "type": "default"}


def test_build_environment_without_uploaded_environment():
    merged = build_environment(None, {"host": "h"})
    assert merged == {"name": "Baseline11 run", "values": [
        {"key": "host", "value": "h", "enabled": True, "type": "default"},
    ]}


def test_build_environment_does_not_mutate_its_input():
    env = {"name": "dev", "values": [{"key": "token", "value": "", "enabled": False}]}
    build_environment(env, {"token": "x"})
    assert env["values"][0] == {"key": "token", "value": "", "enabled": False}


def test_workspace_writes_inputs_and_is_removed_afterwards(tmp_path):
    run_input = _run_input(supplied_values={"token": "s3cret"})
    with newman_workspace(run_input, base_dir=tmp_path) as ws:
        assert json.loads(ws.collection_path.read_text("utf-8")) == run_input.collection_data
        env = json.loads(ws.environment_path.read_text("utf-8"))
        assert env["values"][0]["value"] == "s3cret"
        assert ws.report_path.parent.is_dir()
        assert ws.files_dir.is_dir()
        assert "s3cret" not in str(ws.root)  # never in a filename
        root = ws.root
    assert not root.exists()


def test_workspace_is_removed_even_when_the_body_raises(tmp_path):
    with pytest.raises(RuntimeError), newman_workspace(_run_input(), base_dir=tmp_path) as ws:
        root = ws.root
        raise RuntimeError("boom")
    assert not root.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_workspace_permissions_are_private(tmp_path):
    with newman_workspace(_run_input(), base_dir=tmp_path) as ws:
        assert stat.S_IMODE(ws.root.stat().st_mode) == 0o700
        assert stat.S_IMODE(ws.environment_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(ws.collection_path.stat().st_mode) == 0o600
