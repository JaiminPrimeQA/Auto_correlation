"""A deterministic NewmanRunner test double.

Used by `run_job` tests and by the integration tests to drive the full job
lifecycle without a real Newman process or Docker. `canned_fake_runner` is
what `get_newman_runner` returns when `Settings.newman_runner == "fake"`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..domain.execution_job import NewmanRunner, RunInput, RunOutcome


@dataclass
class FakeNewmanRunner(NewmanRunner):
    outcomes: list[RunOutcome]
    calls: list[RunInput] = field(default_factory=list)
    _call_count: int = field(default=0)

    def run(self, run_input: RunInput) -> RunOutcome:
        self.calls.append(run_input)
        if self._call_count >= len(self.outcomes):
            raise IndexError("Fake runner exhausted")
        result = self.outcomes[self._call_count]
        self._call_count += 1
        return result


def _canned_report_bytes() -> bytes:
    """A minimal Newman report in the shape `tests/fixtures/builders.py`
    produces (a `collection.info.name`, a `run.id`, and one execution with a
    `cursor.position`, `item.name`, `request`, `response`, and `assertions`),
    so it parses cleanly through the real `analysis_service.build_analysis`.
    """
    return json.dumps(
        {
            "collection": {"info": {"name": "Execution"}},
            "run": {
                "id": "fake-run",
                "executions": [
                    {
                        "cursor": {"position": 0},
                        "item": {"name": "Ping"},
                        "request": {"method": "GET", "url": "https://93.184.216.34/ping", "header": []},
                        "response": {
                            "id": "resp-ping",
                            "status": "OK",
                            "code": 200,
                            "header": [],
                            "responseTime": 1,
                            "stream": {"type": "Buffer", "data": []},
                        },
                        "assertions": [],
                    },
                ],
            },
        }
    ).encode()


def canned_fake_runner() -> FakeNewmanRunner:
    """A fresh fake with two canned successful outcomes (baseline, comparison)
    and no dynamic data. A new instance per job: outcomes are consumed per run.
    """
    report_bytes = _canned_report_bytes()
    return FakeNewmanRunner(
        [
            RunOutcome(success=True, report_bytes=report_bytes),
            RunOutcome(success=True, report_bytes=report_bytes),
        ]
    )
