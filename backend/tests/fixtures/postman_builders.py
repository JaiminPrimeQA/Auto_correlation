# backend/tests/fixtures/postman_builders.py
"""Builders for Postman Collection v2.1 / Environment JSON fixtures used in tests."""

from __future__ import annotations


def pm_request(name: str, method: str, url, *, headers=None, body=None, auth=None, events=None) -> dict:
    req: dict = {"method": method, "header": headers or [], "url": url}
    if body is not None:
        req["body"] = body
    if auth is not None:
        req["auth"] = auth
    item: dict = {"name": name, "request": req}
    if events is not None:
        item["event"] = events
    return item


def pm_folder(name: str, items: list) -> dict:
    return {"name": name, "item": items}


def pm_collection(
    name: str,
    items: list,
    *,
    variables: list[dict] | None = None,
    schema: str = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
) -> dict:
    return {
        "info": {"name": name, "schema": schema},
        "item": items,
        "variable": variables or [],
    }


def pm_environment(name: str, values: dict[str, str], *, disabled: set[str] | None = None) -> dict:
    disabled = disabled or set()
    return {
        "name": name,
        "values": [{"key": k, "value": v, "enabled": k not in disabled} for k, v in values.items()],
    }
