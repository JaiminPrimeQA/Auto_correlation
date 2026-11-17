"""Analyses kept in shared storage so any API replica can serve them and a
restart or redeploy does not lose work in progress.

Each analysis is one signed, compressed snapshot:

    analyses/{id}.bin   =  MAGIC | HMAC-SHA256(key, body) | zlib(pickle(state))

The HMAC is checked before anything is unpickled, so a blob written by
anything that does not hold ``B11_STATE_SIGNING_KEY`` is refused rather than
deserialized. Expiry is enforced on read (``expires_at``) and, in S3, by the
bucket's lifecycle rule; the filesystem backend also sweeps in ``cleanup``.

Writes are optimistic: a snapshot remembers the version tag (S3 ETag / file
version) it was read at, and a save fails with ``AnalysisConflict`` when
another replica saved a newer version in between, instead of silently
overwriting that replica's edits.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import pickle
import re
import threading
import time
import uuid
import zlib
from abc import ABC, abstractmethod
from collections import OrderedDict
from pathlib import Path
from typing import Any

from .analysis_store import Analysis, SessionStore

MAGIC = b"B11A1"
_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class AnalysisConflict(Exception):
    """Another replica saved this analysis after it was loaded here."""


class CorruptSnapshot(Exception):
    """The stored snapshot failed its integrity check."""


def _key(analysis_id: str) -> str:
    if not _ID.match(analysis_id):
        raise ValueError("Invalid analysis id for a storage key.")
    return f"analyses/{analysis_id}.bin"


# --------------------------------------------------------------------------- blobs


class BlobBackend(ABC):
    """Minimal versioned blob storage. ``tag`` is an opaque version string."""

    @abstractmethod
    def get(self, key: str) -> tuple[bytes, str] | None: ...

    @abstractmethod
    def head(self, key: str) -> str | None: ...

    @abstractmethod
    def put(self, key: str, data: bytes, *, expected_tag: str | None, create_only: bool) -> str:
        """Write ``data``. With ``create_only`` the key must not exist; with
        ``expected_tag`` the stored version must still be that tag. Raises
        ``AnalysisConflict`` when the precondition fails."""

    @abstractmethod
    def delete(self, key: str) -> bool: ...

    def sweep(self, older_than: float) -> int:  # pragma: no cover - backend specific
        return 0


class FileBlobBackend(BlobBackend):
    """A directory (local disk, or a shared EFS mount for several tasks).
    Version tags are random ids written next to the blob; a process-wide lock
    plus an atomic rename keeps each write all-or-nothing."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _paths(self, key: str) -> tuple[Path, Path]:
        path = self.root / key
        return path, path.with_suffix(path.suffix + ".tag")

    def _tag(self, tag_path: Path) -> str | None:
        try:
            return tag_path.read_text(encoding="ascii").strip() or None
        except FileNotFoundError:
            return None

    def get(self, key: str) -> tuple[bytes, str] | None:
        path, tag_path = self._paths(key)
        with self._lock:
            try:
                data = path.read_bytes()
            except FileNotFoundError:
                return None
            return data, self._tag(tag_path) or ""

    def head(self, key: str) -> str | None:
        path, tag_path = self._paths(key)
        with self._lock:
            return self._tag(tag_path) if path.exists() else None

    def put(self, key: str, data: bytes, *, expected_tag: str | None, create_only: bool) -> str:
        path, tag_path = self._paths(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            current = self._tag(tag_path) if path.exists() else None
            if create_only and current is not None:
                raise AnalysisConflict(key)
            if expected_tag is not None and current != expected_tag:
                raise AnalysisConflict(key)
            tag = uuid.uuid4().hex
            tmp = path.with_suffix(f".{tag}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)
            tag_path.write_text(tag, encoding="ascii")
            return tag

    def delete(self, key: str) -> bool:
        path, tag_path = self._paths(key)
        with self._lock:
            existed = path.exists()
            path.unlink(missing_ok=True)
            tag_path.unlink(missing_ok=True)
            return existed

    def sweep(self, older_than: float) -> int:
        removed = 0
        folder = self.root / "analyses"
        if not folder.exists():
            return 0
        for blob in folder.glob("*.bin"):
            try:
                if blob.stat().st_mtime < older_than:
                    self.delete(f"analyses/{blob.name}")
                    removed += 1
            except FileNotFoundError:
                continue
        return removed


class S3BlobBackend(BlobBackend):
    """Private, KMS-encrypted S3 bucket. Preconditions use S3 conditional
    writes (``If-None-Match: *`` / ``If-Match: <etag>``)."""

    def __init__(self, client: Any, bucket: str, *, kms_key_id: str | None = None) -> None:
        self.client = client
        self.bucket = bucket
        self.kms_key_id = kms_key_id

    @staticmethod
    def _code(exc: Exception) -> str:
        return str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))

    def get(self, key: str) -> tuple[bytes, str] | None:
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=key)
        except Exception as exc:  # botocore ClientError
            if self._code(exc) in {"NoSuchKey", "404", "NotFound"}:
                return None
            raise
        return obj["Body"].read(), str(obj.get("ETag", ""))

    def head(self, key: str) -> str | None:
        try:
            obj = self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            if self._code(exc) in {"NoSuchKey", "404", "NotFound"}:
                return None
            raise
        return str(obj.get("ETag", ""))

    def put(self, key: str, data: bytes, *, expected_tag: str | None, create_only: bool) -> str:
        kwargs: dict[str, Any] = {"Bucket": self.bucket, "Key": key, "Body": data}
        if self.kms_key_id:
            kwargs.update(ServerSideEncryption="aws:kms", SSEKMSKeyId=self.kms_key_id)
        if create_only:
            kwargs["IfNoneMatch"] = "*"
        elif expected_tag:
            kwargs["IfMatch"] = expected_tag
        try:
            out = self.client.put_object(**kwargs)
        except Exception as exc:
            if self._code(exc) in {"PreconditionFailed", "412", "ConditionalRequestConflict", "409"}:
                raise AnalysisConflict(key) from exc
            raise
        return str(out.get("ETag", ""))

    def delete(self, key: str) -> bool:
        existed = self.head(key) is not None
        self.client.delete_object(Bucket=self.bucket, Key=key)
        return existed


# --------------------------------------------------------------------------- store


class PersistentSessionStore(SessionStore):
    """``SessionStore`` over a ``BlobBackend`` with a small per-process cache.

    The cache is only trusted while the stored version tag still matches
    (one cheap HEAD per read); otherwise the snapshot is re-read, so every
    replica sees the latest saved state.
    """

    def __init__(
        self,
        backend: BlobBackend,
        *,
        ttl_seconds: int,
        signing_key: str,
        cache_size: int = 32,
    ) -> None:
        if not signing_key or len(signing_key) < 16:
            raise ValueError("B11_STATE_SIGNING_KEY must be at least 16 characters.")
        self.backend = backend
        self.ttl = ttl_seconds
        self._key = signing_key.encode("utf-8")
        self._cache: OrderedDict[str, tuple[str, Analysis]] = OrderedDict()
        self._cache_size = cache_size
        self._lock = threading.RLock()

    # -- encoding ----------------------------------------------------------

    def _encode(self, analysis: Analysis) -> bytes:
        body = zlib.compress(pickle.dumps(analysis, protocol=pickle.HIGHEST_PROTOCOL), level=6)
        mac = hmac.new(self._key, body, hashlib.sha256).digest()
        return MAGIC + mac + body

    def _decode(self, blob: bytes) -> Analysis:
        if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + 32:
            raise CorruptSnapshot("Unknown snapshot format.")
        mac, body = blob[len(MAGIC): len(MAGIC) + 32], blob[len(MAGIC) + 32:]
        if not hmac.compare_digest(mac, hmac.new(self._key, body, hashlib.sha256).digest()):
            raise CorruptSnapshot("Snapshot signature mismatch.")
        analysis = pickle.loads(zlib.decompress(body))  # noqa: S301 - authenticated above
        if not isinstance(analysis, Analysis):
            raise CorruptSnapshot("Snapshot does not hold an analysis.")
        return analysis

    def _remember(self, analysis_id: str, tag: str, analysis: Analysis) -> None:
        self._cache[analysis_id] = (tag, analysis)
        self._cache.move_to_end(analysis_id)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    # -- SessionStore --------------------------------------------------------

    def create(self, analysis: Analysis) -> None:
        tag = self.backend.put(_key(analysis.id), self._encode(analysis), expected_tag=None, create_only=True)
        with self._lock:
            self._remember(analysis.id, tag, analysis)

    def get(self, analysis_id: str) -> Analysis | None:
        try:
            key = _key(analysis_id)
        except ValueError:
            return None
        with self._lock:
            cached = self._cache.get(analysis_id)
        current = self.backend.head(key)
        if current is None:
            self.discard(analysis_id)
            return None
        if cached is not None and cached[0] == current:
            analysis = cached[1]
        else:
            got = self.backend.get(key)
            if got is None:
                self.discard(analysis_id)
                return None
            blob, tag = got
            try:
                analysis = self._decode(blob)
            except CorruptSnapshot:
                return None
            with self._lock:
                self._remember(analysis_id, tag, analysis)
        if analysis.expires_at < time.time():
            self.delete(analysis_id)
            return None
        return analysis

    def save(self, analysis: Analysis) -> None:
        key = _key(analysis.id)
        with self._lock:
            cached = self._cache.get(analysis.id)
        expected = cached[0] if cached is not None else None
        try:
            tag = self.backend.put(
                key, self._encode(analysis), expected_tag=expected, create_only=False,
            )
        except AnalysisConflict:
            self.discard(analysis.id)
            raise
        with self._lock:
            self._remember(analysis.id, tag, analysis)

    def discard(self, analysis_id: str) -> None:
        with self._lock:
            self._cache.pop(analysis_id, None)

    def delete(self, analysis_id: str) -> bool:
        self.discard(analysis_id)
        try:
            return self.backend.delete(_key(analysis_id))
        except ValueError:
            return False

    def cleanup(self) -> int:
        return self.backend.sweep(time.time() - self.ttl)
