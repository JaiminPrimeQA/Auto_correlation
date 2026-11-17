"""Durable account state: users, sign-in sessions, password resets,
subscriptions, free-use counters and the analysis history of paid users.

Analyses themselves live in the session store (see ``analysis_store``). This
store keeps only what must outlive them.

Two databases sit behind one interface:

* SQLite (``B11_DATABASE_PATH`` is a file path or ``:memory:``) for local
  development and tests - one process only.
* PostgreSQL (``B11_DATABASE_PATH`` is a ``postgresql://`` URL) for
  production, shared by every API replica and the maintenance job. Schema
  changes are versioned migrations applied under an advisory lock, so
  replicas starting together never race.

History files (collection, JMX, manifest) go to a ``HistoryFiles`` backend:
a local folder, or S3 in production.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..core.passwords import new_token, token_digest
from .history_files import HistoryFiles, LocalHistoryFiles

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS password_resets (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS subscriptions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan TEXT NOT NULL,
    price_cents INTEGER NOT NULL,
    currency TEXT NOT NULL,
    payment_provider TEXT NOT NULL,
    payment_ref TEXT NOT NULL,
    purchased_at REAL NOT NULL,
    starts_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    reminder_sent_at REAL,
    expired_notice_sent_at REAL
);
CREATE INDEX IF NOT EXISTS subscriptions_user ON subscriptions(user_id, starts_at);
CREATE TABLE IF NOT EXISTS free_usage (
    usage_key TEXT PRIMARY KEY,
    used INTEGER NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS history (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id TEXT,
    analysis_id TEXT,
    plan TEXT NOT NULL,
    collection_name TEXT NOT NULL,
    collection_filename TEXT,
    collection_bytes INTEGER NOT NULL DEFAULT 0,
    request_count INTEGER,
    correlation_count INTEGER,
    jmx_status TEXT,
    has_collection INTEGER NOT NULL DEFAULT 0,
    has_jmx INTEGER NOT NULL DEFAULT 0,
    has_manifest INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS history_user ON history(user_id, created_at);
CREATE INDEX IF NOT EXISTS history_job ON history(job_id);
CREATE INDEX IF NOT EXISTS history_analysis ON history(analysis_id);
"""

# The files a history entry can hold, by download kind.
HISTORY_FILES = {"collection": "collection.json", "jmx": "plan.jmx", "manifest": "manifest.json"}


class EmailTaken(Exception):
    """An account with this email already exists."""


@dataclass(frozen=True)
class User:
    id: str
    email: str
    name: str
    password_hash: str
    created_at: float


@dataclass(frozen=True)
class Subscription:
    id: str
    user_id: str
    plan: str
    price_cents: int
    currency: str
    payment_provider: str
    payment_ref: str
    purchased_at: float
    starts_at: float
    expires_at: float
    reminder_sent_at: float | None = None
    expired_notice_sent_at: float | None = None


@dataclass(frozen=True)
class HistoryEntry:
    id: str
    user_id: str
    job_id: str | None
    analysis_id: str | None
    plan: str
    collection_name: str
    collection_filename: str | None
    collection_bytes: int
    request_count: int | None
    correlation_count: int | None
    jmx_status: str | None
    has_collection: bool
    has_jmx: bool
    has_manifest: bool
    created_at: float
    updated_at: float
    expires_at: float


def _user(row: dict | None) -> User | None:
    return User(**row) if row else None


def _subscription(row: dict) -> Subscription:
    return Subscription(**row)


def _history(row: dict) -> HistoryEntry:
    data = dict(row)
    for flag in ("has_collection", "has_jmx", "has_manifest"):
        data[flag] = bool(data[flag])
    return HistoryEntry(**data)


# Versioned schema. Append new migrations; never edit an applied one.
MIGRATIONS: list[tuple[int, str]] = [(1, _SCHEMA)]


class _Cursor(Protocol):
    def one(self, sql: str, params: tuple = ()) -> dict | None: ...
    def all(self, sql: str, params: tuple = ()) -> list[dict]: ...
    def run(self, sql: str, params: tuple = ()) -> int: ...


class _Database:
    """The SQL both databases share is written with ``?`` placeholders; the
    PostgreSQL backend rewrites them. ``greatest`` names the two-argument
    maximum (SQLite ``MAX``, PostgreSQL ``GREATEST``)."""

    dialect: str
    greatest: str
    integrity_errors: tuple[type[Exception], ...]

    def one(self, sql: str, params: tuple = ()) -> dict | None:
        raise NotImplementedError

    def all(self, sql: str, params: tuple = ()) -> list[dict]:
        raise NotImplementedError

    def run(self, sql: str, params: tuple = ()) -> int:
        raise NotImplementedError

    def transaction(self, lock_keys: tuple[str, ...] = ()) -> Any:
        raise NotImplementedError

    def migrate(self) -> int:
        raise NotImplementedError

    def ping(self) -> None:
        self.one("SELECT 1 AS ok")

    @contextmanager
    def exclusive(self, name: str) -> Iterator[bool]:
        """Whether this process may run the named singleton job now."""
        yield True

    def close(self) -> None:
        return None


class _SqliteCursor:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def one(self, sql: str, params: tuple = ()) -> dict | None:
        row = self._db.execute(sql, params).fetchone()
        return dict(row) if row is not None else None

    def all(self, sql: str, params: tuple = ()) -> list[dict]:
        return [dict(r) for r in self._db.execute(sql, params).fetchall()]

    def run(self, sql: str, params: tuple = ()) -> int:
        return self._db.execute(sql, params).rowcount


class _SqliteDatabase(_Database):
    """One connection guarded by a lock: SQLite serialises writes anyway."""

    dialect = "sqlite"
    greatest = "MAX"
    integrity_errors = (sqlite3.IntegrityError,)

    def __init__(self, path: str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self._db.execute("PRAGMA journal_mode = WAL")
        self._cursor = _SqliteCursor(self._db)

    def one(self, sql: str, params: tuple = ()) -> dict | None:
        with self._lock:
            return self._cursor.one(sql, params)

    def all(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return self._cursor.all(sql, params)

    def run(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            return self._cursor.run(sql, params)

    @contextmanager
    def transaction(self, lock_keys: tuple[str, ...] = ()) -> Iterator[_Cursor]:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._cursor
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

    def migrate(self) -> int:
        with self._lock:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)"
            )
            done = {r[0] for r in self._db.execute("SELECT version FROM schema_migrations").fetchall()}
            applied = 0
            for version, script in MIGRATIONS:
                if version in done:
                    continue
                self._db.executescript(script)
                self._db.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)", (version, time.time()),
                )
                applied += 1
            return applied

    def close(self) -> None:
        with self._lock:
            self._db.close()


_PLACEHOLDER = re.compile(r"\?")
# Any constant works; it only has to be the same in every replica.
_MIGRATION_LOCK = 0x0B110001


def _pg_sql(sql: str) -> str:
    return _PLACEHOLDER.sub("%s", sql)


def _pg_schema(script: str) -> list[str]:
    script = re.sub(r"\bREAL\b", "DOUBLE PRECISION", script)
    return [stmt.strip() for stmt in script.split(";") if stmt.strip()]


class _PgCursor:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def one(self, sql: str, params: tuple = ()) -> dict | None:
        cur = self._conn.execute(_pg_sql(sql), params)
        return cur.fetchone() if cur.description else None

    def all(self, sql: str, params: tuple = ()) -> list[dict]:
        return list(self._conn.execute(_pg_sql(sql), params).fetchall())

    def run(self, sql: str, params: tuple = ()) -> int:
        return int(self._conn.execute(_pg_sql(sql), params).rowcount)


class _PostgresDatabase(_Database):
    """A connection pool shared by the request threads of one replica."""

    dialect = "postgresql"
    greatest = "GREATEST"

    def __init__(self, url: str, *, pool_size: int = 10) -> None:
        import psycopg
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        self.integrity_errors = (psycopg.errors.IntegrityError,)
        self._pool = ConnectionPool(
            url, min_size=1, max_size=max(pool_size, 1), open=True, timeout=10,
            kwargs={"autocommit": True, "row_factory": dict_row},
            check=ConnectionPool.check_connection,
        )

    def one(self, sql: str, params: tuple = ()) -> dict | None:
        with self._pool.connection() as conn:
            return _PgCursor(conn).one(sql, params)

    def all(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._pool.connection() as conn:
            return _PgCursor(conn).all(sql, params)

    def run(self, sql: str, params: tuple = ()) -> int:
        with self._pool.connection() as conn:
            return _PgCursor(conn).run(sql, params)

    @contextmanager
    def transaction(self, lock_keys: tuple[str, ...] = ()) -> Iterator[_Cursor]:
        with self._pool.connection() as conn, conn.transaction():
            # Per-key advisory locks serialise concurrent updates of the same
            # counters across replicas; sorted to rule out deadlocks.
            for key in sorted(set(lock_keys)):
                conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (key,))
            yield _PgCursor(conn)

    def migrate(self) -> int:
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK,))
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            done = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
            applied = 0
            for version, script in MIGRATIONS:
                if version in done:
                    continue
                for statement in _pg_schema(script):
                    conn.execute(statement)
                conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
                applied += 1
            return applied

    @contextmanager
    def exclusive(self, name: str) -> Iterator[bool]:
        # A session-level advisory lock held for the job's duration: only one
        # replica runs it; the others skip this round instead of waiting.
        with self._pool.connection() as conn:
            row = conn.execute("SELECT pg_try_advisory_lock(hashtext(%s)) AS got", (name,)).fetchone()
            got = bool(row and row["got"])
            try:
                yield got
            finally:
                if got:
                    conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (name,))

    def close(self) -> None:
        self._pool.close()


def is_postgres_url(database: str) -> bool:
    return database.startswith(("postgresql://", "postgres://"))


def open_database(database: str, *, pool_size: int = 10) -> _Database:
    if is_postgres_url(database):
        return _PostgresDatabase(database, pool_size=pool_size)
    return _SqliteDatabase(database)


class AccountStore:
    def __init__(
        self,
        database: str,
        history: str | HistoryFiles,
        *,
        auto_migrate: bool = True,
        pool_size: int = 10,
    ) -> None:
        self._sql = open_database(database, pool_size=pool_size)
        self._files: HistoryFiles = LocalHistoryFiles(history) if isinstance(history, str) else history
        if auto_migrate:
            self._sql.migrate()

    @property
    def dialect(self) -> str:
        return self._sql.dialect

    def migrate(self) -> int:
        return self._sql.migrate()

    def ping(self) -> None:
        """Raise when the database is unreachable (readiness check)."""
        self._sql.ping()

    def exclusive(self, name: str) -> Any:
        """Context manager yielding True in exactly one replica at a time."""
        return self._sql.exclusive(name)

    def close(self) -> None:
        self._sql.close()

    def _one(self, sql: str, params: tuple = ()) -> dict | None:
        return self._sql.one(sql, params)

    def _all(self, sql: str, params: tuple = ()) -> list[dict]:
        return self._sql.all(sql, params)

    def _run(self, sql: str, params: tuple = ()) -> int:
        return self._sql.run(sql, params)

    # --- users ---

    def create_user(self, *, email: str, name: str, password_hash: str, now: float) -> User:
        user = User(id=uuid.uuid4().hex, email=email.lower(), name=name, password_hash=password_hash, created_at=now)
        try:
            self._run(
                "INSERT INTO users (id, email, name, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                (user.id, user.email, user.name, user.password_hash, user.created_at),
            )
        except self._sql.integrity_errors as exc:
            raise EmailTaken(email) from exc
        return user

    def get_user(self, user_id: str) -> User | None:
        return _user(self._one("SELECT * FROM users WHERE id = ?", (user_id,)))

    def ensure_external_user(self, *, subject: str, email: str | None, name: str | None, now: float) -> User:
        """The account linked to an identity-provider subject, created on first
        sign-in. It has no usable password. It is never merged into an existing
        password account by email: an IdP that does not verify addresses could
        otherwise take that account over."""
        import hashlib

        user_id = "oidc_" + hashlib.sha256(subject.encode("utf-8")).hexdigest()[:32]
        existing = self.get_user(user_id)
        if existing is not None:
            return existing
        address = (email or "").strip().lower()
        if not address or self.get_user_by_email(address) is not None:
            address = f"{user_id}@users.invalid"
        try:
            self._run(
                "INSERT INTO users (id, email, name, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, address, (name or address.split("@")[0])[:120], "!external", now),
            )
        except self._sql.integrity_errors:
            pass  # created concurrently by another request or replica
        user = self.get_user(user_id)
        assert user is not None
        return user

    def get_user_by_email(self, email: str) -> User | None:
        return _user(self._one("SELECT * FROM users WHERE email = ?", (email.lower(),)))

    def set_password(self, user_id: str, password_hash: str) -> None:
        self._run("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))

    # --- sign-in sessions ---

    def create_session(self, user_id: str, *, now: float, ttl_seconds: float) -> str:
        token = new_token()
        self._run(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token_digest(token), user_id, now, now + ttl_seconds),
        )
        return token

    def session_user(self, token: str, *, now: float) -> User | None:
        return _user(self._one(
            "SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id "
            "WHERE sessions.token_hash = ? AND sessions.expires_at > ?",
            (token_digest(token), now),
        ))

    def delete_session(self, token: str) -> None:
        self._run("DELETE FROM sessions WHERE token_hash = ?", (token_digest(token),))

    def delete_user_sessions(self, user_id: str) -> None:
        self._run("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    # --- password resets ---

    def create_password_reset(self, user_id: str, *, now: float, ttl_seconds: float) -> str:
        token = new_token()
        with self._sql.transaction() as tx:
            # Only the newest link works.
            tx.run("DELETE FROM password_resets WHERE user_id = ?", (user_id,))
            tx.run(
                "INSERT INTO password_resets (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (token_digest(token), user_id, now + ttl_seconds),
            )
        return token

    def consume_password_reset(self, token: str, *, now: float) -> str | None:
        """The user id the token was issued for, once; None if unknown or expired."""
        # One statement, so two replicas can never both redeem the same link.
        row = self._one(
            "DELETE FROM password_resets WHERE token_hash = ? RETURNING user_id, expires_at", (token_digest(token),),
        )
        if row is None or row["expires_at"] <= now:
            return None
        return str(row["user_id"])

    def purge_expired_tokens(self, *, now: float) -> int:
        removed = self._run("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        removed += self._run("DELETE FROM password_resets WHERE expires_at <= ?", (now,))
        return removed

    # --- subscriptions ---

    def add_subscription(self, sub: Subscription) -> None:
        self._run(
            "INSERT INTO subscriptions (id, user_id, plan, price_cents, currency, payment_provider, payment_ref, "
            "purchased_at, starts_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sub.id, sub.user_id, sub.plan, sub.price_cents, sub.currency, sub.payment_provider, sub.payment_ref,
             sub.purchased_at, sub.starts_at, sub.expires_at),
        )

    def list_subscriptions(self, user_id: str) -> list[Subscription]:
        rows = self._all("SELECT * FROM subscriptions WHERE user_id = ? ORDER BY starts_at DESC", (user_id,))
        return [_subscription(r) for r in rows]

    def active_subscription(self, user_id: str, *, now: float) -> Subscription | None:
        row = self._one(
            "SELECT * FROM subscriptions WHERE user_id = ? AND starts_at <= ? AND expires_at > ? "
            "ORDER BY starts_at DESC LIMIT 1",
            (user_id, now, now),
        )
        return _subscription(row) if row else None

    def upcoming_subscriptions(self, user_id: str, *, now: float) -> list[Subscription]:
        rows = self._all(
            "SELECT * FROM subscriptions WHERE user_id = ? AND starts_at > ? ORDER BY starts_at", (user_id, now),
        )
        return [_subscription(r) for r in rows]

    def latest_expiry(self, user_id: str) -> float | None:
        row = self._one("SELECT MAX(expires_at) AS latest FROM subscriptions WHERE user_id = ?", (user_id,))
        return row["latest"] if row and row["latest"] is not None else None

    def subscriptions_due_reminder(self, *, now: float, window_seconds: float) -> list[Subscription]:
        """Plans ending within the window that have not been reminded about and
        are not followed by another plan (a renewal needs no reminder)."""
        rows = self._all(
            "SELECT * FROM subscriptions s WHERE s.reminder_sent_at IS NULL AND s.starts_at <= ? "
            "AND s.expires_at > ? AND s.expires_at <= ? AND NOT EXISTS ("
            "  SELECT 1 FROM subscriptions n WHERE n.user_id = s.user_id AND n.id != s.id "
            "  AND n.starts_at >= s.starts_at AND n.expires_at > s.expires_at)",
            (now, now, now + window_seconds),
        )
        return [_subscription(r) for r in rows]

    def subscriptions_due_expiry_notice(self, *, now: float) -> list[Subscription]:
        rows = self._all(
            "SELECT * FROM subscriptions s WHERE s.expired_notice_sent_at IS NULL AND s.expires_at <= ? "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM subscriptions n WHERE n.user_id = s.user_id AND n.id != s.id "
            "  AND n.starts_at >= s.starts_at AND n.expires_at > s.expires_at)",
            (now,),
        )
        return [_subscription(r) for r in rows]

    def mark_reminder_sent(self, subscription_id: str, *, now: float) -> None:
        self._run("UPDATE subscriptions SET reminder_sent_at = ? WHERE id = ?", (now, subscription_id))

    def mark_expiry_notice_sent(self, subscription_id: str, *, now: float) -> None:
        self._run("UPDATE subscriptions SET expired_notice_sent_at = ? WHERE id = ?", (now, subscription_id))

    # --- free uses ---

    def free_uses_used(self, keys: list[str], *, _cursor: _Cursor | None = None) -> int:
        """The most uses recorded against any of the keys."""
        if not keys:
            return 0
        placeholders = ",".join("?" * len(keys))
        row = (_cursor or self._sql).one(
            f"SELECT MAX(used) AS used FROM free_usage WHERE usage_key IN ({placeholders})", tuple(keys),
        )
        return int(row["used"]) if row and row["used"] is not None else 0

    def consume_free_use(self, keys: list[str], *, limit: int, now: float) -> bool:
        """Atomically record one use against every key, unless any key has
        already reached the limit."""
        with self._sql.transaction(lock_keys=tuple(f"free:{k}" for k in keys)) as tx:
            if self.free_uses_used(keys, _cursor=tx) >= limit:
                return False
            for key in keys:
                tx.run(
                    "INSERT INTO free_usage (usage_key, used, updated_at) VALUES (?, 1, ?) "
                    "ON CONFLICT(usage_key) DO UPDATE SET used = free_usage.used + 1, "
                    "updated_at = excluded.updated_at",
                    (key, now),
                )
            return True

    def refund_free_use(self, keys: list[str], *, now: float) -> None:
        for key in keys:
            self._run(
                f"UPDATE free_usage SET used = {self._sql.greatest}(used - 1, 0), updated_at = ? WHERE usage_key = ?",
                (now, key),
            )

    # --- history ---

    def entry_dir(self, entry_id: str) -> Path:
        """The local folder of an entry (local history files only)."""
        if not isinstance(self._files, LocalHistoryFiles):
            raise TypeError("History files are not stored on local disk.")
        return self._files.entry_dir(entry_id)

    def create_history(
        self, *, user_id: str, plan: str, collection_name: str, now: float, retention_seconds: float,
        job_id: str | None = None, analysis_id: str | None = None, collection_filename: str | None = None,
        collection_raw: bytes | None = None,
    ) -> HistoryEntry:
        entry_id = uuid.uuid4().hex
        if collection_raw is not None:
            self._files.write(entry_id, HISTORY_FILES["collection"], collection_raw)
        self._run(
            "INSERT INTO history (id, user_id, job_id, analysis_id, plan, collection_name, collection_filename, "
            "collection_bytes, has_collection, created_at, updated_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (entry_id, user_id, job_id, analysis_id, plan, collection_name, collection_filename,
             len(collection_raw or b""), int(collection_raw is not None), now, now, now + retention_seconds),
        )
        entry = self.get_history(entry_id)
        assert entry is not None
        return entry

    def get_history(self, entry_id: str) -> HistoryEntry | None:
        row = self._one("SELECT * FROM history WHERE id = ?", (entry_id,))
        return _history(row) if row else None

    def history_for_job(self, user_id: str, job_id: str) -> HistoryEntry | None:
        row = self._one("SELECT * FROM history WHERE user_id = ? AND job_id = ?", (user_id, job_id))
        return _history(row) if row else None

    def history_for_analysis(self, user_id: str, analysis_id: str) -> HistoryEntry | None:
        row = self._one("SELECT * FROM history WHERE user_id = ? AND analysis_id = ?", (user_id, analysis_id))
        return _history(row) if row else None

    def list_history(self, user_id: str, *, now: float) -> list[HistoryEntry]:
        rows = self._all(
            "SELECT * FROM history WHERE user_id = ? AND expires_at > ? ORDER BY created_at DESC", (user_id, now),
        )
        return [_history(r) for r in rows]

    _UPDATABLE = frozenset({"analysis_id", "collection_name", "request_count", "correlation_count", "jmx_status"})

    def update_history(self, entry_id: str, *, now: float, **fields: object) -> None:
        unknown = set(fields) - self._UPDATABLE
        if unknown:
            raise ValueError(f"Not updatable: {sorted(unknown)}")
        if not fields:
            return
        assignments = ", ".join(f"{name} = ?" for name in fields)
        self._run(
            f"UPDATE history SET {assignments}, updated_at = ? WHERE id = ?", (*fields.values(), now, entry_id),
        )

    def save_history_file(self, entry_id: str, kind: str, data: bytes, *, now: float) -> None:
        self._files.write(entry_id, HISTORY_FILES[kind], data)
        self._run(f"UPDATE history SET has_{kind} = 1, updated_at = ? WHERE id = ?", (now, entry_id))

    def read_history_file(self, entry_id: str, kind: str) -> bytes | None:
        return self._files.read(entry_id, HISTORY_FILES[kind])

    def delete_history(self, entry_id: str) -> None:
        self._run("DELETE FROM history WHERE id = ?", (entry_id,))
        self._files.delete(entry_id)

    def extend_history_retention(self, user_id: str, *, retention_seconds: float, now: float) -> None:
        """A longer plan keeps the user's current (unexpired) history longer too."""
        self._run(
            f"UPDATE history SET expires_at = {self._sql.greatest}(expires_at, created_at + ?) "
            "WHERE user_id = ? AND expires_at > ?",
            (retention_seconds, user_id, now),
        )

    def purge_expired_history(self, *, now: float) -> int:
        ids = [r["id"] for r in self._all("SELECT id FROM history WHERE expires_at <= ?", (now,))]
        for entry_id in ids:
            self.delete_history(entry_id)
        return len(ids)
