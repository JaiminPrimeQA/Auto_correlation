"""Shared test fixtures."""

from __future__ import annotations

import pytest

from app.core.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings()


# Accounts, plans and free-use counters live in SQLite. Tests get a private
# in-memory database and history folder, no background maintenance thread,
# and a fresh store (and so a fresh free allowance) per test.
import os  # noqa: E402
import tempfile  # noqa: E402

os.environ.setdefault("B11_DATABASE_PATH", ":memory:")
os.environ.setdefault("B11_HISTORY_DIR", tempfile.mkdtemp(prefix="b11-history-"))
os.environ.setdefault("B11_MAINTENANCE_ENABLED", "false")
# Never send real email from tests, whatever a local .env configures.
for _name in ("B11_SMTP_HOST", "B11_SMTP_USERNAME", "B11_SMTP_PASSWORD", "B11_MAIL_FROM_ADDRESS"):
    os.environ[_name] = ""


@pytest.fixture(autouse=True)
def _fresh_account_store():
    from app.api.deps import get_account_store

    get_account_store.cache_clear()
    yield
    get_account_store.cache_clear()
