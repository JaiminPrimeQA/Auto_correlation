"""Where execution jobs actually run.

A dedicated, bounded thread pool - not the web server's shared thread pool -
so long Newman runs can never starve request handling, and at most
`Settings.max_concurrent_executions` jobs execute at once (extra jobs wait,
still QUEUED). `run_job` never raises, so nothing is lost in the futures.
Phase 5 swaps this for a queue + ECS/Fargate worker behind the same seam.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol


class JobDispatcher(Protocol):
    def submit(self, fn: Callable[..., None], /, *args: Any, **kwargs: Any) -> None: ...


class ThreadPoolJobDispatcher:
    def __init__(self, max_workers: int) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="b11-job")

    def submit(self, fn: Callable[..., None], /, *args: Any, **kwargs: Any) -> None:
        self._executor.submit(fn, *args, **kwargs)

    def shutdown(self, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=True)


class InlineJobDispatcher:
    """Runs the job synchronously on the calling thread. Tests only."""

    def submit(self, fn: Callable[..., None], /, *args: Any, **kwargs: Any) -> None:
        fn(*args, **kwargs)
