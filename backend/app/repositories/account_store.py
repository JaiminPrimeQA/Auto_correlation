"""Durable account state in SQLite: users, sign-in sessions, password resets,
subscriptions, free-use counters and the analysis history of paid users.

Analyses themselves stay ephemeral (see ``analysis_store``). This store keeps
only what must outlive them. History files live on disk under
``history_dir/<entry id>/``; the database holds their metadata.

One connection guarded by a lock: SQLite serialises writes anyway, and the
volumes involved are small. It suits a single API instance; several
instances need a shared database behind the same interface.
"""

from __future__ import annotations

import shutil
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..core.passwords import new_token, token_digest

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


def _user(row: sqlite3.Row | None) -> User | None:
    return User(**dict(row)) if row else None


def _subscription(row: sqlite3.Row) -> Subscription:
    return Subscription(**dict(row))


def _history(row: sqlite3.Row) -> HistoryEntry:
    data = dict(row)
    for flag in ("has_collection", "has_jmx", "has_manifest"):
        data[flag] = bool(data[flag])
    return HistoryEntry(**data)


class AccountStore:
    def __init__(self, database_path: str, history_dir: str) -> None:
        if database_path != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self._history_dir = Path(history_dir)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(database_path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        if database_path != ":memory:":
            self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._db.execute(sql, params).fetchone()

    def _all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    def _run(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            return self._db.execute(sql, params).rowcount

    # --- users ---

    def create_user(self, *, email: str, name: str, password_hash: str, now: float) -> User:
        user = User(id=uuid.uuid4().hex, email=email.lower(), name=name, password_hash=password_hash, created_at=now)
        try:
            self._run(
                "INSERT INTO users (id, email, name, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                (user.id, user.email, user.name, user.password_hash, user.created_at),
            )
        except sqlite3.IntegrityError as exc:
            raise EmailTaken(email) from exc
        return user

    def get_user(self, user_id: str) -> User | None:
        return _user(self._one("SELECT * FROM users WHERE id = ?", (user_id,)))

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
        with self._lock:
            # Only the newest link works.
            self._db.execute("DELETE FROM password_resets WHERE user_id = ?", (user_id,))
            self._db.execute(
                "INSERT INTO password_resets (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (token_digest(token), user_id, now + ttl_seconds),
            )
        return token

    def consume_password_reset(self, token: str, *, now: float) -> str | None:
        """The user id the token was issued for, once; None if unknown or expired."""
        digest = token_digest(token)
        with self._lock:
            row = self._db.execute(
                "SELECT user_id, expires_at FROM password_resets WHERE token_hash = ?", (digest,),
            ).fetchone()
            self._db.execute("DELETE FROM password_resets WHERE token_hash = ?", (digest,))
        if row is None or row["expires_at"] <= now:
            return None
        return str(row["user_id"])

    def purge_expired_tokens(self, *, now: float) -> int:
        with self._lock:
            removed = self._db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,)).rowcount
            removed += self._db.execute("DELETE FROM password_resets WHERE expires_at <= ?", (now,)).rowcount
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

    def free_uses_used(self, keys: list[str]) -> int:
        """The most uses recorded against any of the keys."""
        if not keys:
            return 0
        placeholders = ",".join("?" * len(keys))
        row = self._one(f"SELECT MAX(used) AS used FROM free_usage WHERE usage_key IN ({placeholders})", tuple(keys))
        return int(row["used"]) if row and row["used"] is not None else 0

    def consume_free_use(self, keys: list[str], *, limit: int, now: float) -> bool:
        """Atomically record one use against every key, unless any key has
        already reached the limit."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                if self.free_uses_used(keys) >= limit:
                    self._db.execute("ROLLBACK")
                    return False
                for key in keys:
                    self._db.execute(
                        "INSERT INTO free_usage (usage_key, used, updated_at) VALUES (?, 1, ?) "
                        "ON CONFLICT(usage_key) DO UPDATE SET used = used + 1, updated_at = excluded.updated_at",
                        (key, now),
                    )
                self._db.execute("COMMIT")
                return True
            except Exception:
                self._db.execute("ROLLBACK")
                raise

    def refund_free_use(self, keys: list[str], *, now: float) -> None:
        for key in keys:
            self._run(
                "UPDATE free_usage SET used = MAX(used - 1, 0), updated_at = ? WHERE usage_key = ?", (now, key),
            )

    # --- history ---

    def entry_dir(self, entry_id: str) -> Path:
        return self._history_dir / entry_id

    def create_history(
        self, *, user_id: str, plan: str, collection_name: str, now: float, retention_seconds: float,
        job_id: str | None = None, analysis_id: str | None = None, collection_filename: str | None = None,
        collection_raw: bytes | None = None,
    ) -> HistoryEntry:
        entry_id = uuid.uuid4().hex
        if collection_raw is not None:
            folder = self.entry_dir(entry_id)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / HISTORY_FILES["collection"]).write_bytes(collection_raw)
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
        folder = self.entry_dir(entry_id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / HISTORY_FILES[kind]).write_bytes(data)
        self._run(f"UPDATE history SET has_{kind} = 1, updated_at = ? WHERE id = ?", (now, entry_id))

    def read_history_file(self, entry_id: str, kind: str) -> bytes | None:
        path = self.entry_dir(entry_id) / HISTORY_FILES[kind]
        return path.read_bytes() if path.is_file() else None

    def delete_history(self, entry_id: str) -> None:
        self._run("DELETE FROM history WHERE id = ?", (entry_id,))
        shutil.rmtree(self.entry_dir(entry_id), ignore_errors=True)

    def extend_history_retention(self, user_id: str, *, retention_seconds: float, now: float) -> None:
        """A longer plan keeps the user's current (unexpired) history longer too."""
        self._run(
            "UPDATE history SET expires_at = MAX(expires_at, created_at + ?) WHERE user_id = ? AND expires_at > ?",
            (retention_seconds, user_id, now),
        )

    def purge_expired_history(self, *, now: float) -> int:
        ids = [r["id"] for r in self._all("SELECT id FROM history WHERE expires_at <= ?", (now,))]
        for entry_id in ids:
            self.delete_history(entry_id)
        return len(ids)
