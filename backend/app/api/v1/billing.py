"""Plans, checkout and purchases, plus the saved analysis history of paid
plans (list, download, delete)."""

from __future__ import annotations

import math
import re
import time

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ...core.config import Settings, get_settings
from ...core.errors import ProblemException, not_found
from ...core.logging import get_logger
from ...repositories.account_store import HISTORY_FILES, AccountStore, HistoryEntry, User
from ...services import billing, emails
from ...services.billing import DAY, PurchaseError
from ...services.mailer import Mailer
from ..deps import get_account_store, get_mailer
from .accounts import require_user

router = APIRouter(tags=["billing"])
log = get_logger("billing")


class CheckoutRequest(BaseModel):
    plan: str = Field(max_length=32)


@router.get("/billing/plans")
async def plans(settings: Settings = Depends(get_settings)) -> dict:
    return billing.plans_dto(settings)


@router.post("/billing/checkout", status_code=201)
async def checkout(
    body: CheckoutRequest,
    user: User = Depends(require_user),
    settings: Settings = Depends(get_settings),
    store: AccountStore = Depends(get_account_store),
    mailer: Mailer = Depends(get_mailer),
) -> dict:
    now = time.time()
    try:
        plan, sub = billing.purchase(store, settings, user, body.plan, now=now)
    except PurchaseError as exc:
        raise ProblemException(status=400, code="purchase_failed", title="Purchase failed", detail=str(exc)) from exc
    mailer.send(emails.purchase_confirmation(settings, user, plan, sub))
    log.info(f"plan purchased: {plan.id}", extra={"stage": "checkout"})
    return {"subscription": billing.subscription_dto(sub, settings, now=now)}


@router.get("/billing/subscriptions")
async def subscriptions(
    user: User = Depends(require_user),
    settings: Settings = Depends(get_settings),
    store: AccountStore = Depends(get_account_store),
) -> dict:
    now = time.time()
    return {"items": [billing.subscription_dto(s, settings, now=now) for s in store.list_subscriptions(user.id)]}


def _history_dto(entry: HistoryEntry, *, now: float) -> dict:
    downloads = {
        kind: f"/api/v1/history/{entry.id}/download/{kind}"
        for kind, present in (("collection", entry.has_collection), ("jmx", entry.has_jmx),
                              ("manifest", entry.has_manifest))
        if present
    }
    return {
        "id": entry.id,
        "collection_name": entry.collection_name,
        "collection_filename": entry.collection_filename,
        "collection_bytes": entry.collection_bytes,
        "request_count": entry.request_count,
        "correlation_count": entry.correlation_count,
        "jmx_status": entry.jmx_status,
        "plan": entry.plan,
        "created_at": entry.created_at,
        "expires_at": entry.expires_at,
        "days_left": max(0, math.ceil((entry.expires_at - now) / DAY)),
        "downloads": downloads,
    }


def _owned_entry(entry_id: str, user: User, store: AccountStore) -> HistoryEntry:
    entry = store.get_history(entry_id)
    if entry is None or entry.user_id != user.id or entry.expires_at <= time.time():
        raise not_found("History entry not found or expired.")
    return entry


@router.get("/history")
async def list_history(user: User = Depends(require_user), store: AccountStore = Depends(get_account_store)) -> dict:
    now = time.time()
    return {"items": [_history_dto(e, now=now) for e in store.list_history(user.id, now=now)]}


_MEDIA = {"collection": "application/json", "jmx": "application/xml", "manifest": "application/json"}


def _filename(entry: HistoryEntry, kind: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", entry.collection_name.lower()).strip("-")[:60] or "baseline11"
    if kind == "collection":
        return f"{slug}.postman_collection.json"
    if kind == "jmx":
        return f"{slug}-{'validated' if entry.jmx_status == 'validated' else 'generated'}.jmx"
    return f"{slug}-manifest.json"


@router.get("/history/{entry_id}/download/{kind}")
async def download_history_file(
    entry_id: str, kind: str, user: User = Depends(require_user), store: AccountStore = Depends(get_account_store),
) -> Response:
    if kind not in HISTORY_FILES:
        raise not_found("Unknown file.")
    entry = _owned_entry(entry_id, user, store)
    data = store.read_history_file(entry.id, kind)
    if data is None:
        raise not_found("This file was not saved for this analysis.")
    return Response(
        content=data,
        media_type=_MEDIA[kind],
        headers={
            "Content-Disposition": f'attachment; filename="{_filename(entry, kind)}"',
            "Cache-Control": "no-store",
        },
    )


@router.delete("/history/{entry_id}", status_code=204)
async def delete_history_entry(
    entry_id: str, user: User = Depends(require_user), store: AccountStore = Depends(get_account_store),
) -> None:
    entry = _owned_entry(entry_id, user, store)
    store.delete_history(entry.id)
