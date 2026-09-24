import pytest

from app.domain.execution_job import RunInput, RunOutcome
from app.services.fake_newman_runner import FakeNewmanRunner


def _run_input(**overrides) -> RunInput:
    base = dict(collection_data={"info": {"name": "x"}, "item": []}, environment_data=None,
                supplied_values={}, folder_id=None, timeout_seconds=300)
    base.update(overrides)
    return RunInput(**base)


def test_returns_outcomes_in_order():
    outcomes = [RunOutcome(success=True, report_bytes=b"1"), RunOutcome(success=True, report_bytes=b"2")]
    runner = FakeNewmanRunner(outcomes)
    assert runner.run(_run_input()) is outcomes[0]
    assert runner.run(_run_input()) is outcomes[1]


def test_records_every_call():
    runner = FakeNewmanRunner([RunOutcome(success=True), RunOutcome(success=True)])
    a, b = _run_input(folder_id="auth"), _run_input(supplied_values={"host": "x"})
    runner.run(a)
    runner.run(b)
    assert runner.calls == [a, b]


def test_accepts_and_records_the_should_cancel_callable():
    runner = FakeNewmanRunner([RunOutcome(success=True)])

    def never() -> bool:
        return False

    runner.run(_run_input(), should_cancel=never)
    assert runner.cancel_checks == [never]


def test_raises_if_called_more_times_than_outcomes_configured():
    runner = FakeNewmanRunner([RunOutcome(success=True)])
    runner.run(_run_input())
    with pytest.raises(IndexError):
        runner.run(_run_input())
