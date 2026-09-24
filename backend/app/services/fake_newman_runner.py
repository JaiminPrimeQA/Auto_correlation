"""A deterministic NewmanRunner test double.

Used by Task 6's `run_job` tests and by the Task 12 integration tests to
drive the full job lifecycle without a real Newman process or Docker.
"""

from __future__ import annotations

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
