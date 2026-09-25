import threading

from app.services.job_dispatcher import InlineJobDispatcher, ThreadPoolJobDispatcher


def test_inline_dispatcher_runs_immediately_on_the_calling_thread():
    seen = []
    InlineJobDispatcher().submit(lambda x, *, y: seen.append((x, y, threading.current_thread())), 1, y=2)
    assert seen == [(1, 2, threading.current_thread())]


def test_thread_pool_dispatcher_runs_off_the_calling_thread():
    done = threading.Event()
    seen = []

    def work():
        seen.append(threading.current_thread())
        done.set()

    dispatcher = ThreadPoolJobDispatcher(max_workers=1)
    try:
        dispatcher.submit(work)
        assert done.wait(timeout=5)
        assert seen[0] is not threading.current_thread()
    finally:
        dispatcher.shutdown(wait=True)


def test_thread_pool_dispatcher_never_exceeds_max_workers():
    release = threading.Event()
    running = []
    lock = threading.Lock()
    peak = {"value": 0}
    started = threading.Semaphore(0)

    def work():
        with lock:
            running.append(1)
            peak["value"] = max(peak["value"], len(running))
        started.release()
        release.wait(timeout=5)
        with lock:
            running.pop()

    dispatcher = ThreadPoolJobDispatcher(max_workers=2)
    try:
        for _ in range(5):
            dispatcher.submit(work)
        assert started.acquire(timeout=5) and started.acquire(timeout=5)
        assert not started.acquire(timeout=0.2)  # a third job is queued, not running
        release.set()
    finally:
        dispatcher.shutdown(wait=True)
    assert peak["value"] == 2
