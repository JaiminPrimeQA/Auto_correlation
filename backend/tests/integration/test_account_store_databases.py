"""The account store behaves the same on SQLite and PostgreSQL.

PostgreSQL runs only when ``B11_TEST_DATABASE_URL`` points at a disposable
database, e.g.::

    docker run -d --rm --name b11-test-pg -e POSTGRES_PASSWORD=b11test \\
        -e POSTGRES_DB=b11 -p 55432:5432 postgres:16-alpine
    B11_TEST_DATABASE_URL=postgresql://postgres:b11test@localhost:55432/b11 pytest
"""

from __future__ import annotations

import os
import threading
import uuid

import pytest

from app.repositories.account_store import AccountStore, EmailTaken, Subscription

PG_URL = os.environ.get("B11_TEST_DATABASE_URL", "")
NOW = 1_800_000_000.0


def _reset_pg() -> None:
    import psycopg

    with psycopg.connect(PG_URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")


@pytest.fixture(params=["sqlite", "postgresql"])
def store(request, tmp_path):
    if request.param == "postgresql":
        if not PG_URL:
            pytest.skip("B11_TEST_DATABASE_URL not set")
        _reset_pg()
        s = AccountStore(PG_URL, str(tmp_path / "history"))
    else:
        s = AccountStore(str(tmp_path / "accounts.db"), str(tmp_path / "history"))
    yield s
    s.close()


def _user(store: AccountStore, email: str = "ada@example.com"):
    return store.create_user(email=email, name="Ada", password_hash="hash", now=NOW)


def test_users_and_duplicate_emails(store):
    user = _user(store)
    assert store.get_user(user.id) == user
    assert store.get_user_by_email("ADA@example.com") == user
    with pytest.raises(EmailTaken):
        _user(store, "Ada@Example.com")


def test_external_user_email_race_does_not_merge_accounts_or_fail(store, monkeypatch):
    original = _user(store)
    # Simulate a competing signup committing just after the email check.
    monkeypatch.setattr(store, "get_user_by_email", lambda email: None)
    external = store.ensure_external_user(subject="other-subject", email=original.email, name=None, now=NOW)
    assert external.id != original.id
    assert external.email.endswith("@users.invalid")


def test_sessions_expire(store):
    user = _user(store)
    token = store.create_session(user.id, now=NOW, ttl_seconds=60)
    assert store.session_user(token, now=NOW + 1) == user
    assert store.session_user(token, now=NOW + 61) is None
    assert store.purge_expired_tokens(now=NOW + 61) == 1


def test_password_reset_link_works_once(store):
    user = _user(store)
    old = store.create_password_reset(user.id, now=NOW, ttl_seconds=600)
    token = store.create_password_reset(user.id, now=NOW, ttl_seconds=600)
    assert store.consume_password_reset(old, now=NOW) is None  # superseded
    assert store.consume_password_reset(token, now=NOW + 1) == user.id
    assert store.consume_password_reset(token, now=NOW + 2) is None


def test_free_uses_stop_at_the_limit_even_under_concurrency(store):
    keys = ["acct:x", "device:y"]
    results: list[bool] = []
    lock = threading.Lock()

    def use():
        ok = store.consume_free_use(keys, limit=3, now=NOW)
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=use) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 3
    assert store.free_uses_used(keys) == 3
    store.refund_free_use(keys, now=NOW)
    assert store.free_uses_used(keys) == 2
    for _ in range(5):
        store.refund_free_use(keys, now=NOW)
    assert store.free_uses_used(keys) == 0  # never negative


def test_subscriptions_and_history_retention(store):
    user = _user(store)
    sub = Subscription(
        id=uuid.uuid4().hex, user_id=user.id, plan="monthly", price_cents=900, currency="USD",
        payment_provider="demo", payment_ref="ref", purchased_at=NOW, starts_at=NOW, expires_at=NOW + 100,
    )
    store.add_subscription(sub)
    assert store.active_subscription(user.id, now=NOW + 1) == sub
    assert store.latest_expiry(user.id) == NOW + 100
    assert [s.id for s in store.subscriptions_due_reminder(now=NOW + 50, window_seconds=60)] == [sub.id]

    entry = store.create_history(
        user_id=user.id, plan="monthly", collection_name="Orders", now=NOW, retention_seconds=10,
        collection_raw=b"{}",
    )
    store.save_history_file(entry.id, "jmx", b"<jmx/>", now=NOW)
    assert store.read_history_file(entry.id, "jmx") == b"<jmx/>"
    store.extend_history_retention(user.id, retention_seconds=1000, now=NOW + 1)
    refreshed = store.get_history(entry.id)
    assert refreshed is not None and refreshed.expires_at == NOW + 1000 and refreshed.has_jmx
    assert store.purge_expired_history(now=NOW + 2000) == 1
    assert store.read_history_file(entry.id, "jmx") is None


def test_migrations_are_idempotent(store):
    assert store.migrate() == 0


@pytest.mark.skipif(not PG_URL, reason="B11_TEST_DATABASE_URL not set")
def test_replicas_starting_together_migrate_once(tmp_path):
    _reset_pg()
    errors: list[BaseException] = []
    stores: list[AccountStore] = []

    def start():
        try:
            stores.append(AccountStore(PG_URL, str(tmp_path / "h"), pool_size=2))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=start) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert all(s.migrate() == 0 for s in stores)
    for s in stores:
        s.close()
