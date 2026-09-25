import json

from app.services.newman_report_adapter import read_generated_report

_VALID = json.dumps({"collection": {"info": {"name": "C"}}, "run": {"executions": []}}).encode()


def test_valid_report_returns_its_exact_bytes(tmp_path):
    path = tmp_path / "report.json"
    path.write_bytes(_VALID)
    outcome = read_generated_report(path, max_bytes=1024)
    assert outcome.success is True
    assert outcome.report_bytes == _VALID


def test_missing_report(tmp_path):
    outcome = read_generated_report(tmp_path / "report.json", max_bytes=1024)
    assert (outcome.success, outcome.error_code) == (False, "missing_report")


def test_empty_report_is_missing(tmp_path):
    path = tmp_path / "report.json"
    path.write_bytes(b"")
    assert read_generated_report(path, max_bytes=1024).error_code == "missing_report"


def test_oversized_report_is_rejected_without_reading(tmp_path, monkeypatch):
    path = tmp_path / "report.json"
    path.write_bytes(b"x" * 2048)
    from pathlib import Path

    def _no_read(self):
        raise AssertionError("must not read an oversized report")

    monkeypatch.setattr(Path, "read_bytes", _no_read)
    outcome = read_generated_report(path, max_bytes=1024)
    assert (outcome.success, outcome.error_code) == (False, "report_too_large")


def test_invalid_json_is_malformed(tmp_path):
    path = tmp_path / "report.json"
    path.write_bytes(b"{not json")
    assert read_generated_report(path, max_bytes=1024).error_code == "malformed_report"


def test_report_without_run_executions_is_malformed(tmp_path):
    path = tmp_path / "report.json"
    path.write_bytes(json.dumps({"run": {}}).encode())
    assert read_generated_report(path, max_bytes=1024).error_code == "malformed_report"


def test_error_details_are_fixed_messages(tmp_path):
    path = tmp_path / "report.json"
    path.write_bytes(b'{"secret-looking": "abc123"')
    outcome = read_generated_report(path, max_bytes=1024)
    assert "abc123" not in (outcome.error_detail or "")
