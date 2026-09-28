"""Accounts: register, sign in/out, password reset, and what the caller may
do right now (``GET /account/me``, which also answers for anonymous visitors).

The session is an httpOnly, SameSite=Lax cookie holding a random token; only
its hash is stored. Sign-in errors never reveal whether an email has an
account.
"""

from __future__ import annotations

import re
import time
from functools import lru_cache

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from ...core.config import Settings, get_settings
from ...core.errors import ProblemException, rate_limited, unauthorized, validation_error
from ...core.logging import get_logger
from ...core.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    burn_verification_time,
    hash_password,
    verify_password,
)
from ...core.security import RateLimiter
from ...repositories.account_store import AccountStore, EmailTaken, User
from ...services import billing, emails
from ...services.billing import Entitlement
from ...services.mailer import Mailer
from ..deps import (
    SESSION_COOKIE,
    client_ip,
    get_account_store,
    get_current_user,
    get_entitlement,
    get_mailer,
)

router = APIRouter(tags=["account"])
log = get_logger("accounts")

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


def _email(value: str) -> str:
    value = value.strip().lower()
    if len(value) > 254 or not _EMAIL.match(value):
        raise ValueError("Enter a valid email address.")
    return value


def _password(value: str) -> str:
    if len(value) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Use at least {MIN_PASSWORD_LENGTH} characters.")
    if len(value) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"Use at most {MAX_PASSWORD_LENGTH} characters.")
    return value


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        return _email(v)

    @field_validator("password")
    @classmethod
    def _valid_password(cls, v: str) -> str:
        return _password(v)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("Enter your name.")
        return v


class LoginRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=MAX_PASSWORD_LENGTH)


class ForgotPasswordRequest(BaseModel):
    email: str = Field(max_length=254)


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str

    @field_validator("password")
    @classmethod
    def _valid_password(cls, v: str) -> str:
        return _password(v)


@lru_cache
def _login_limiter() -> RateLimiter:
    s = get_settings()
    return RateLimiter(s.login_attempts, s.login_window_seconds)


def _throttle(request: Request, settings: Settings, action: str) -> None:
    if settings.rate_limit_enabled and not _login_limiter().allow(f"{action}:{client_ip(request, settings)}"):
        raise rate_limited("Too many attempts. Wait a few minutes and try again.")


def _set_session(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, max_age=settings.account_session_days * 86400, httponly=True, samesite="lax",
        secure=settings.is_production or settings.cookie_secure, path="/",
    )


def _me(ent: Entitlement, store: AccountStore, settings: Settings) -> dict:
    return billing.entitlement_dto(ent, store, settings, now=time.time())


def require_user(user: User | None = Depends(get_current_user)) -> User:
    if user is None:
        raise unauthorized("Sign in to continue.")
    return user


@router.get("/account/me")
async def me(
    ent: Entitlement = Depends(get_entitlement),
    store: AccountStore = Depends(get_account_store),
    settings: Settings = Depends(get_settings),
) -> dict:
    return _me(ent, store, settings)


@router.post("/auth/register", status_code=201)
async def register(
    body: RegisterRequest,
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    store: AccountStore = Depends(get_account_store),
    mailer: Mailer = Depends(get_mailer),
) -> dict:
    _throttle(request, settings, "register")
    password_hash = await run_in_threadpool(hash_password, body.password)
    now = time.time()
    try:
        user = store.create_user(email=body.email, name=body.name, password_hash=password_hash, now=now)
    except EmailTaken as exc:
        raise ProblemException(
            status=409, code="email_taken", title="Account exists",
            detail="An account with this email already exists. Sign in instead.",
        ) from exc
    token = store.create_session(user.id, now=now, ttl_seconds=settings.account_session_days * 86400)
    _set_session(response, token, settings)
    mailer.send(emails.welcome(settings, user))
    log.info("account registered", extra={"stage": "register"})
    return {"user": billing.user_dto(user)}


@router.post("/auth/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    store: AccountStore = Depends(get_account_store),
) -> dict:
    _throttle(request, settings, "login")
    user = store.get_user_by_email(body.email.strip())
    if user is None:
        await run_in_threadpool(burn_verification_time, body.password)
        ok = False
    else:
        ok = await run_in_threadpool(verify_password, body.password, user.password_hash)
    if not ok or user is None:
        raise unauthorized("The email or password is incorrect.")
    token = store.create_session(user.id, now=time.time(), ttl_seconds=settings.account_session_days * 86400)
    _set_session(response, token, settings)
    return {"user": billing.user_dto(user)}


@router.post("/auth/logout", status_code=204)
async def logout(
    request: Request, response: Response, store: AccountStore = Depends(get_account_store),
) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        store.delete_session(token)
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.post("/auth/forgot-password", status_code=202)
async def forgot_password(
    body: ForgotPasswordRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    store: AccountStore = Depends(get_account_store),
    mailer: Mailer = Depends(get_mailer),
) -> dict:
    _throttle(request, settings, "forgot")
    user = store.get_user_by_email(body.email.strip())
    if user is not None:
        token = store.create_password_reset(
            user.id, now=time.time(), ttl_seconds=settings.password_reset_minutes * 60,
        )
        mailer.send(emails.password_reset(settings, user, token))
    # The same answer whether or not the account exists.
    return {"detail": "If an account uses this email, a reset link is on its way."}


@router.post("/auth/reset-password")
async def reset_password(
    body: ResetPasswordRequest,
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    store: AccountStore = Depends(get_account_store),
) -> dict:
    _throttle(request, settings, "reset")
    now = time.time()
    user_id = store.consume_password_reset(body.token, now=now)
    user = store.get_user(user_id) if user_id else None
    if user is None:
        raise validation_error("This reset link is invalid or has expired. Request a new one.")
    store.set_password(user.id, await run_in_threadpool(hash_password, body.password))
    # Signs out every other browser, then signs this one in.
    store.delete_user_sessions(user.id)
    token = store.create_session(user.id, now=now, ttl_seconds=settings.account_session_days * 86400)
    _set_session(response, token, settings)
    return {"user": billing.user_dto(user)}
