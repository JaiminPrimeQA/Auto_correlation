"""Plans in use: what a caller may do (entitlement), purchases, and the
analysis history kept for paid plans.

Every analysis needs a signed-in account (``require_account``). Free
allowance: ``free_uses`` analyses with files up to ``free_max_file_mb``,
counted per account, per browser and per client IP (optional), so registering
a second account in the same browser does not reset it. A paid plan lifts
the use limit, raises the file limit and keeps a history.
"""

from __future__ import annotations

import math
import secrets
import uuid
from dataclasses import dataclass

from ..core.config import MIB, Settings
from ..core.errors import ProblemException
from ..domain.plans import Plan, add_months, free_max_file_bytes, get_plan, plan_catalog
from ..repositories.account_store import AccountStore, Subscription, User

DAY = 86400


@dataclass(frozen=True)
class Entitlement:
    user: User | None
    subscription: Subscription | None
    plan: Plan | None
    max_file_bytes: int
    free_limit: int
    free_used: int
    usage_keys: tuple[str, ...]
    sign_in_required: bool = False

    @property
    def paid(self) -> bool:
        return self.plan is not None

    @property
    def uses_remaining(self) -> int | None:
        """None means unlimited (a paid plan)."""
        return None if self.paid else max(0, self.free_limit - self.free_used)


def usage_keys(*, user: User | None, device_id: str | None, client_ip: str | None, settings: Settings) -> tuple[str, ...]:
    keys = []
    if device_id:
        keys.append(f"device:{device_id}")
    if client_ip and settings.free_tier_count_by_ip:
        keys.append(f"ip:{client_ip}")
    if user is not None:
        keys.append(f"user:{user.id}")
    return tuple(keys)


def resolve_entitlement(
    store: AccountStore, settings: Settings, *, user: User | None, device_id: str | None,
    client_ip: str | None, now: float,
) -> Entitlement:
    keys = usage_keys(user=user, device_id=device_id, client_ip=client_ip, settings=settings)
    sub = store.active_subscription(user.id, now=now) if user else None
    plan = get_plan(sub.plan, settings) if sub else None
    return Entitlement(
        user=user,
        subscription=sub if plan else None,
        plan=plan,
        max_file_bytes=plan.max_file_bytes if plan else free_max_file_bytes(settings),
        free_limit=settings.free_uses,
        free_used=store.free_uses_used(list(keys)),
        usage_keys=keys,
        sign_in_required=user is None and settings.require_account and settings.auth_mode != "oidc",
    )


def _mb(size: int) -> str:
    return f"{size / MIB:.1f} MB" if size < 10 * MIB else f"{size / MIB:.0f} MB"


def usage_limit_reached(ent: Entitlement) -> ProblemException:
    return ProblemException(
        status=402, code="usage_limit_reached", title="Free limit reached",
        detail=(f"You have used all {ent.free_limit} free analyses. Choose a plan to keep going with larger "
                "files and saved history."),
    )


def sign_in_required(ent: Entitlement) -> ProblemException:
    return ProblemException(
        status=401, code="sign_in_required", title="Sign in required",
        detail=(f"Create a free account or sign in to start. The free plan includes {ent.free_limit} analyses "
                f"with files up to {_mb(ent.max_file_bytes)}."),
    )


def ensure_can_start(ent: Entitlement) -> None:
    if ent.sign_in_required:
        raise sign_in_required(ent)
    if not ent.paid and ent.free_used >= ent.free_limit:
        raise usage_limit_reached(ent)


def ensure_file_fits(ent: Entitlement, *, filename: str, size: int) -> None:
    if size <= ent.max_file_bytes:
        return
    limit = _mb(ent.max_file_bytes)
    where = f"The {ent.plan.name} plan" if ent.plan else "The free plan"
    upgrade = " Choose a larger plan to upload it." if not ent.plan or ent.plan.id != "yearly" else ""
    raise ProblemException(
        status=413, code="plan_file_limit", title="File too large for your plan",
        detail=f"'{filename}' is {_mb(size)}. {where} accepts files up to {limit}.{upgrade}",
    )


def request_settings(settings: Settings, ent: Entitlement) -> Settings:
    """Settings for one request, with the parser size limits set to the
    caller's plan limit (the plan limit is checked first, with a clearer
    message; these keep the parsers consistent with it)."""
    limit = ent.max_file_bytes
    return settings.model_copy(update={
        "max_collection_bytes": limit,
        "max_environment_bytes": limit,
        "max_file_bytes": limit,
        "max_buffer_length": max(settings.max_buffer_length, limit),
    })


def consume_use(store: AccountStore, ent: Entitlement, *, now: float) -> None:
    """Record one analysis against the free allowance (no-op on a paid plan).
    Atomic: two concurrent starts cannot both take the last free use."""
    if ent.paid:
        return
    if not store.consume_free_use(list(ent.usage_keys), limit=ent.free_limit, now=now):
        raise usage_limit_reached(ent)


def refund_use(store: AccountStore, ent: Entitlement, *, now: float) -> None:
    if not ent.paid:
        store.refund_free_use(list(ent.usage_keys), now=now)


# --- purchases ---


class PurchaseError(Exception):
    pass


def purchase(store: AccountStore, settings: Settings, user: User, plan_id: str, *, now: float) -> tuple[Plan, Subscription]:
    """Buy a plan. With a plan already running (or queued) the new one starts
    when the last one ends, so no paid time is lost."""
    if settings.billing_provider == "disabled":
        raise PurchaseError("Purchases are not available right now.")
    plan = get_plan(plan_id, settings)
    if plan is None:
        raise PurchaseError(f"Unknown plan '{plan_id}'.")
    starts_at = max(now, store.latest_expiry(user.id) or now)
    sub = Subscription(
        id=uuid.uuid4().hex,
        user_id=user.id,
        plan=plan.id,
        price_cents=plan.price_cents,
        currency=plan.currency,
        payment_provider=settings.billing_provider,
        payment_ref=f"{settings.billing_provider}_{secrets.token_hex(6)}",
        purchased_at=now,
        starts_at=starts_at,
        expires_at=add_months(starts_at, plan.months),
    )
    store.add_subscription(sub)
    if starts_at <= now:
        store.extend_history_retention(user.id, retention_seconds=plan.retention_days * DAY, now=now)
    return plan, sub


def plans_dto(settings: Settings) -> dict:
    return {
        "currency": settings.plan_currency.upper(),
        "billing_provider": settings.billing_provider,
        "free": {"uses": settings.free_uses, "max_file_mb": settings.free_max_file_mb},
        "plans": [
            {"id": p.id, "name": p.name, "months": p.months, "price_cents": p.price_cents, "currency": p.currency,
             "max_file_mb": p.max_file_mb, "retention_days": p.retention_days}
            for p in plan_catalog(settings)
        ],
    }


def subscription_dto(sub: Subscription, settings: Settings, *, now: float) -> dict:
    plan = get_plan(sub.plan, settings)
    if sub.starts_at > now:
        status = "scheduled"
    elif sub.expires_at > now:
        status = "active"
    else:
        status = "expired"
    return {
        "id": sub.id,
        "plan": sub.plan,
        "plan_name": plan.name if plan else sub.plan,
        "status": status,
        "price_cents": sub.price_cents,
        "currency": sub.currency,
        "payment_provider": sub.payment_provider,
        "payment_ref": sub.payment_ref,
        "purchased_at": sub.purchased_at,
        "starts_at": sub.starts_at,
        "expires_at": sub.expires_at,
        "days_left": max(0, math.ceil((sub.expires_at - now) / DAY)) if status == "active" else None,
    }


def user_dto(user: User) -> dict:
    return {"id": user.id, "email": user.email, "name": user.name, "created_at": user.created_at}


def entitlement_dto(ent: Entitlement, store: AccountStore, settings: Settings, *, now: float) -> dict:
    return {
        "user": user_dto(ent.user) if ent.user else None,
        "sign_in_required": ent.sign_in_required,
        "plan": subscription_dto(ent.subscription, settings, now=now) if ent.subscription else None,
        "upcoming": [
            subscription_dto(s, settings, now=now) for s in store.upcoming_subscriptions(ent.user.id, now=now)
        ] if ent.user else [],
        "limits": {
            "max_file_bytes": ent.max_file_bytes,
            "max_file_mb": ent.max_file_bytes // MIB,
            "uses_limit": None if ent.paid else ent.free_limit,
            "uses_used": None if ent.paid else min(ent.free_used, ent.free_limit),
            "uses_remaining": ent.uses_remaining,
            "history_retention_days": ent.plan.retention_days if ent.plan else None,
        },
    }
