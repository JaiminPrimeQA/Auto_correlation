"""The emails the app sends, each as plain text with an HTML alternative.

User-supplied text (names) is HTML-escaped. Dates are shown in UTC.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime
from email.message import EmailMessage

from ..core.config import Settings
from ..domain.plans import Plan, format_price
from ..repositories.account_store import Subscription, User
from .mailer import sender

_ACCENT = "#2f5bea"


def format_date(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%d %b %Y")


def _link(settings: Settings, path: str) -> str:
    return settings.app_base_url.rstrip("/") + path


def _html(title: str, paragraphs: list[str], *, button: tuple[str, str] | None = None,
          rows: list[tuple[str, str]] | None = None) -> str:
    body = "".join(f'<p style="margin:0 0 14px;line-height:1.55">{p}</p>' for p in paragraphs)
    if rows:
        cells = "".join(
            f'<tr><td style="padding:6px 0;color:#64748b">{html.escape(k)}</td>'
            f'<td style="padding:6px 0;text-align:right;font-weight:600">{html.escape(v)}</td></tr>'
            for k, v in rows
        )
        body += (
            '<table role="presentation" width="100%" style="border-collapse:collapse;margin:4px 0 18px;'
            f'border-top:1px solid #e2e8f0;border-bottom:1px solid #e2e8f0">{cells}</table>'
        )
    if button:
        label, url = button
        body += (
            f'<p style="margin:8px 0 18px"><a href="{html.escape(url)}" style="background:{_ACCENT};color:#fff;'
            'text-decoration:none;padding:11px 18px;border-radius:8px;font-weight:600;display:inline-block">'
            f"{html.escape(label)}</a></p>"
        )
    return (
        '<!doctype html><html><body style="margin:0;background:#f4f6fa;font-family:-apple-system,Segoe UI,'
        'Roboto,Helvetica,Arial,sans-serif;color:#0f172a">'
        '<table role="presentation" width="100%" style="padding:28px 12px"><tr><td align="center">'
        '<table role="presentation" width="100%" style="max-width:560px;background:#fff;border:1px solid #e2e8f0;'
        'border-radius:14px;padding:28px">'
        f'<tr><td><p style="margin:0 0 18px;font-weight:700;color:{_ACCENT}">Baseline Auto Correlation</p>'
        f'<h1 style="margin:0 0 16px;font-size:20px">{html.escape(title)}</h1>{body}'
        '<p style="margin:18px 0 0;font-size:12px;color:#94a3b8">You received this email because of your '
        "Baseline Auto Correlation account.</p></td></tr></table></td></tr></table></body></html>"
    )


def _text(paragraphs: list[str], *, rows: list[tuple[str, str]] | None = None, link: str | None = None) -> str:
    parts = list(paragraphs)
    if rows:
        parts.append("\n".join(f"{k}: {v}" for k, v in rows))
    if link:
        parts.append(link)
    parts.append("Baseline Auto Correlation")
    return "\n\n".join(parts) + "\n"


def _message(settings: Settings, to: User, subject: str, text: str, html_body: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender(settings)
    message["To"] = to.email
    message.set_content(text)
    message.add_alternative(html_body, subtype="html")
    return message


def _plan_rows(plan: Plan, sub: Subscription) -> list[tuple[str, str]]:
    return [
        ("Plan", plan.name),
        ("Starts", format_date(sub.starts_at)),
        ("Ends", format_date(sub.expires_at)),
        ("File size limit", f"{plan.max_file_mb} MB per file"),
        ("History kept for", f"{plan.retention_days} days"),
    ]


def welcome(settings: Settings, user: User) -> EmailMessage:
    name = html.escape(user.name)
    url = _link(settings, "/pricing")
    lines = [
        "Your account is ready. Choose a plan to upload larger files and keep a history of your analyses.",
    ]
    return _message(
        settings, user, "Welcome to Baseline Auto Correlation",
        _text([f"Hi {user.name},", *lines], link=url),
        _html(f"Welcome, {user.name}", [f"Hi {name},", *lines], button=("See the plans", url)),
    )


def purchase_confirmation(settings: Settings, user: User, plan: Plan, sub: Subscription) -> EmailMessage:
    name = html.escape(user.name)
    url = _link(settings, "/dashboard")
    rows = [*_plan_rows(plan, sub), ("Amount", format_price(sub.price_cents, sub.currency)),
            ("Reference", sub.payment_ref)]
    starts_later = sub.starts_at > sub.purchased_at + 60
    lines = [
        f"Thank you for purchasing the {plan.name} plan.",
        (f"It starts on {format_date(sub.starts_at)}, when your current plan ends."
         if starts_later else "It is active now."),
    ]
    return _message(
        settings, user, f"Your {plan.name} plan is confirmed",
        _text([f"Hi {user.name},", *lines], rows=rows, link=url),
        _html("Purchase confirmed", [f"Hi {name},", *lines], rows=rows, button=("Open your dashboard", url)),
    )


def expiry_reminder(settings: Settings, user: User, plan: Plan, sub: Subscription, *, now: float) -> EmailMessage:
    name = html.escape(user.name)
    days = max(1, round((sub.expires_at - now) / 86400))
    when = "tomorrow" if days == 1 else f"in {days} days"
    url = _link(settings, "/pricing")
    lines = [
        f"Your {plan.name} plan ends {when}, on {format_date(sub.expires_at)}.",
        "Renew before then to keep your file limit and history without a break. After the plan ends your "
        "account returns to the free allowance and saved history is removed when its retention period ends.",
    ]
    return _message(
        settings, user, f"Your {plan.name} plan ends {when}",
        _text([f"Hi {user.name},", *lines], link=url),
        _html("Your plan ends soon", [f"Hi {name},", *lines], button=("Renew your plan", url)),
    )


def plan_ended(settings: Settings, user: User, plan: Plan, sub: Subscription) -> EmailMessage:
    name = html.escape(user.name)
    url = _link(settings, "/pricing")
    lines = [
        f"Your {plan.name} plan ended on {format_date(sub.expires_at)}.",
        "Your account now has the free allowance. Choose a plan any time to continue with larger files and "
        "saved history.",
    ]
    return _message(
        settings, user, f"Your {plan.name} plan has ended",
        _text([f"Hi {user.name},", *lines], link=url),
        _html("Your plan has ended", [f"Hi {name},", *lines], button=("Choose a plan", url)),
    )


def password_reset(settings: Settings, user: User, token: str) -> EmailMessage:
    name = html.escape(user.name)
    url = _link(settings, f"/reset-password?token={token}")
    minutes = settings.password_reset_minutes
    lines = [
        "We received a request to reset your password.",
        f"The link works once and expires in {minutes} minutes. If you did not ask for this, ignore this email.",
    ]
    return _message(
        settings, user, "Reset your password",
        _text([f"Hi {user.name},", *lines], link=url),
        _html("Reset your password", [f"Hi {name},", *lines], button=("Choose a new password", url)),
    )
