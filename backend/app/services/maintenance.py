"""Periodic account maintenance: plan-expiry reminders, plan-ended notices,
removal of history past its retention, and cleanup of expired sign-in and
password-reset tokens.

``run_maintenance`` is one pass (pure apart from the store and mailer, so it
is tested directly); ``MaintenanceLoop`` repeats it on a daemon thread.
Each email is marked sent before the next pass, so a reminder goes out once.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from ..core.config import Settings
from ..core.logging import get_logger
from ..domain.plans import get_plan
from ..repositories.account_store import AccountStore
from . import emails
from .billing import DAY
from .mailer import Mailer

log = get_logger("maintenance")


@dataclass
class MaintenanceResult:
    reminders: int = 0
    expiry_notices: int = 0
    history_removed: int = 0
    tokens_removed: int = 0


def run_maintenance(store: AccountStore, mailer: Mailer, settings: Settings, *, now: float) -> MaintenanceResult:
    result = MaintenanceResult()
    window = settings.expiry_reminder_days * DAY
    for sub in store.subscriptions_due_reminder(now=now, window_seconds=window):
        user, plan = store.get_user(sub.user_id), get_plan(sub.plan, settings)
        store.mark_reminder_sent(sub.id, now=now)
        if user and plan:
            mailer.send(emails.expiry_reminder(settings, user, plan, sub, now=now))
            result.reminders += 1
    for sub in store.subscriptions_due_expiry_notice(now=now):
        user, plan = store.get_user(sub.user_id), get_plan(sub.plan, settings)
        store.mark_expiry_notice_sent(sub.id, now=now)
        # A plan that ended long ago (e.g. the server was down) gets no late notice.
        if user and plan and now - sub.expires_at < 7 * DAY:
            mailer.send(emails.plan_ended(settings, user, plan, sub))
            result.expiry_notices += 1
    result.history_removed = store.purge_expired_history(now=now)
    result.tokens_removed = store.purge_expired_tokens(now=now)
    return result


class MaintenanceLoop:
    def __init__(self, store: AccountStore, mailer: Mailer, settings: Settings) -> None:
        self._store, self._mailer, self._settings = store, mailer, settings
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                r = run_maintenance(self._store, self._mailer, self._settings, now=time.time())
                if r.reminders or r.expiry_notices or r.history_removed:
                    log.info(
                        f"maintenance: {r.reminders} reminder(s), {r.expiry_notices} expiry notice(s), "
                        f"{r.history_removed} history entr(y/ies) removed",
                        extra={"stage": "maintenance"},
                    )
            except Exception as exc:  # noqa: BLE001 - the loop must survive one bad pass
                log.error("maintenance pass failed", extra={"stage": "maintenance", "exc_type": type(exc).__name__})
            self._stop.wait(self._settings.maintenance_interval_seconds)

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="account-maintenance", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
