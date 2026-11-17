"""Apply pending account-database migrations, then exit.

    python -m app.migrate

Run it as a one-off ECS task (same image and settings as the API) before a
release that changes the schema. API tasks also migrate at start-up under an
advisory lock (``B11_DATABASE_AUTO_MIGRATE``), so this is optional, but a
separate step makes a failing migration stop the release before any new task
takes traffic.
"""

from __future__ import annotations

import sys

from .core.config import get_settings
from .core.logging import configure_logging, get_logger
from .repositories.account_store import AccountStore

log = get_logger("migrate")


def main() -> int:
    configure_logging()
    settings = get_settings()
    store = AccountStore(settings.account_database, settings.history_dir, auto_migrate=False, pool_size=1)
    try:
        applied = store.migrate()
    finally:
        store.close()
    log.info(f"migrations applied: {applied}", extra={"stage": "migrate"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
