"""Where saved-history files (collection, JMX, manifest) are kept.

``LocalHistoryFiles`` is a folder per entry (development). ``S3HistoryFiles``
keeps them in a private, KMS-encrypted bucket under ``history/{entry id}/``
so every API replica can serve them and they survive task replacement.
Retention is enforced by the account store's purge (which deletes the
objects); a bucket lifecycle rule is the safety net.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, Protocol

_SAFE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def _safe(part: str) -> str:
    if not _SAFE.match(part) or part in {".", ".."}:
        raise ValueError("Invalid history path component.")
    return part


class HistoryFiles(Protocol):
    def write(self, entry_id: str, name: str, data: bytes) -> None: ...
    def read(self, entry_id: str, name: str) -> bytes | None: ...
    def delete(self, entry_id: str) -> None: ...


class LocalHistoryFiles:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def entry_dir(self, entry_id: str) -> Path:
        return self.root / _safe(entry_id)

    def write(self, entry_id: str, name: str, data: bytes) -> None:
        folder = self.entry_dir(entry_id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / _safe(name)).write_bytes(data)

    def read(self, entry_id: str, name: str) -> bytes | None:
        path = self.entry_dir(entry_id) / _safe(name)
        return path.read_bytes() if path.is_file() else None

    def delete(self, entry_id: str) -> None:
        shutil.rmtree(self.entry_dir(entry_id), ignore_errors=True)


class S3HistoryFiles:
    def __init__(self, client: Any, bucket: str, *, kms_key_id: str | None = None, prefix: str = "history/") -> None:
        self.client = client
        self.bucket = bucket
        self.kms_key_id = kms_key_id
        self.prefix = prefix

    def _key(self, entry_id: str, name: str = "") -> str:
        base = f"{self.prefix}{_safe(entry_id)}/"
        return base + _safe(name) if name else base

    def write(self, entry_id: str, name: str, data: bytes) -> None:
        kwargs: dict[str, Any] = {"Bucket": self.bucket, "Key": self._key(entry_id, name), "Body": data}
        if self.kms_key_id:
            kwargs.update(ServerSideEncryption="aws:kms", SSEKMSKeyId=self.kms_key_id)
        self.client.put_object(**kwargs)

    def read(self, entry_id: str, name: str) -> bytes | None:
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=self._key(entry_id, name))
        except Exception as exc:  # botocore ClientError
            code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
            if code in {"NoSuchKey", "404", "NotFound"}:
                return None
            raise
        return bytes(obj["Body"].read())

    def delete(self, entry_id: str) -> None:
        prefix = self._key(entry_id)
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": keys, "Quiet": True})
