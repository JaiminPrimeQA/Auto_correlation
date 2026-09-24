"""Extract the folder tree from a Postman collection, with per-folder request counts."""

from __future__ import annotations

import re

from ..domain.postman_models import PostmanFolder

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(path: str) -> str:
    return _SLUG_RE.sub("-", path.lower()).strip("-") or "folder"


def count_requests(items: list) -> int:
    total = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        if isinstance(it.get("item"), list):
            total += count_requests(it["item"])
        elif "request" in it:
            total += 1
    return total


def _walk(items: list, path: str) -> list[PostmanFolder]:
    folders: list[PostmanFolder] = []
    for it in items:
        if not isinstance(it, dict) or not isinstance(it.get("item"), list):
            continue
        name = it.get("name", "unnamed")
        here = f"{path} > {name}" if path else name
        folders.append(PostmanFolder(id=_slugify(here), name=name, path=here, request_count=count_requests(it["item"])))
        folders.extend(_walk(it["item"], here))
    return folders


def extract_folders(collection_data: dict) -> list[PostmanFolder]:
    return _walk(collection_data.get("item", []), "")
