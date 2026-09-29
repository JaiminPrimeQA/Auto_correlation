"""The emails the app sends, each as plain text with an HTML alternative.

The HTML uses one branded layout: the Baseline11 logo (sent inline as a CID
image, since most email clients block SVG and remote images), a navy-to-blue
banner, the body, and optional feature highlights. User-supplied text (names)
is HTML-escaped. Dates are shown in UTC.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime
from email.message import EmailMessage
from functools import cache
from pathlib import Path

from ..core.config import Settings
from ..domain.plans import Plan, format_price, get_plan
from ..repositories.account_store import Subscription, User
from .mailer import sender

_LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "email" / "logo.png"
_LOGO_CID = "baseline11-logo"

# Brand colours from the Baseline11 logo.
_NAVY = "#17158a"
_BLUE = "#036ed8"
_GAUGE = "linear-gradient(90deg,#ff0000 0%,#ffd447 51%,#4db648 100%)"
_FONT = "-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"

# The product in three steps, shown in the welcome email.
_STEPS = [
    ("Upload your Postman collection", "We run it twice with Newman in an isolated container."),
    ("Review the dynamic values",
     "Tokens, IDs and session values that change between runs are found and explained with evidence."),
    ("Download a JMeter test plan", "Get a JMX with the extractors wired in, ready for JMeter 5.6.3."),
]


def format_date(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%d %b %Y")


def _link(settings: Settings, path: str) -> str:
    return settings.app_base_url.rstrip("/") + path


def _button(label: str, url: str) -> str:
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" style="margin:6px 0 4px"><tr>'
        f'<td bgcolor="{_BLUE}" style="border-radius:10px">'
        f'<a href="{html.escape(url)}" style="display:inline-block;padding:14px 26px;font-size:15px;'
        f'font-weight:700;color:#ffffff;text-decoration:none;border-radius:10px">{html.escape(label)}</a>'
        "</td></tr></table>"
    )


def _label(text: str, *, margin: str) -> str:
    return (f'<p style="margin:{margin};font-size:12px;font-weight:700;letter-spacing:.08em;'
            f'text-transform:uppercase;color:{_NAVY}">{html.escape(text)}</p>')


def _rows_table(heading: str | None, rows: list[tuple[str, str]]) -> str:
    cells = "".join(
        '<tr><td style="padding:9px 0;color:#64748b;font-size:14px;border-top:1px solid #e2e8f0">'
        f"{html.escape(k)}</td>"
        '<td style="padding:9px 0;text-align:right;font-size:14px;font-weight:600;color:#0f172a;'
        f'border-top:1px solid #e2e8f0">{html.escape(v)}</td></tr>'
        for k, v in rows
    )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:8px 0 24px;'
        'background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px"><tr><td style="padding:16px 20px 8px">'
        f'{_label(heading, margin="0 0 6px") if heading else ""}'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="border-collapse:collapse">{cells}</table></td></tr></table>'
    )


def _features(heading: str, items: list[tuple[str, str]], *, numbered: bool) -> str:
    rows = "".join(
        '<tr><td valign="top" width="42" style="padding:0 0 16px">'
        '<div style="width:28px;height:28px;line-height:28px;border-radius:14px;background:#e6f0fc;'
        f'color:{_BLUE};font-size:13px;font-weight:700;text-align:center">{i if numbered else "&#10003;"}</div></td>'
        '<td valign="top" style="padding:3px 0 16px"><p style="margin:0;font-size:15px;font-weight:700;'
        f'color:#0f172a">{html.escape(title)}</p><p style="margin:3px 0 0;font-size:14px;line-height:1.5;'
        f'color:#64748b">{html.escape(desc)}</p></td></tr>'
        for i, (title, desc) in enumerate(items, start=1)
    )
    return (
        '<tr><td style="padding:0 36px"><div style="border-top:1px solid #e2e8f0;font-size:0">&nbsp;</div></td></tr>'
        f'<tr><td style="padding:24px 36px 12px">{_label(heading, margin="0 0 18px")}'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table></td></tr>'
    )


def _html(settings: Settings, *, eyebrow: str, title: str, intro: str, paragraphs: list[str],
          button: tuple[str, str] | None = None, rows: list[tuple[str, str]] | None = None,
          rows_heading: str | None = None, callout: str | None = None,
          features: tuple[str, list[tuple[str, str]], bool] | None = None) -> str:
    """The shared layout. ``paragraphs`` and ``callout`` are HTML (escape user
    text first); every other string is escaped here."""
    body = "".join(
        f'<p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:#334155">{p}</p>' for p in paragraphs
    )
    if callout:
        body += (
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:4px 0 22px">'
            f'<tr><td style="padding:14px 18px;background:#eef5fe;border-left:4px solid {_BLUE};'
            f'border-radius:8px;font-size:14px;line-height:1.55;color:#1e3a5f">{callout}</td></tr></table>'
        )
    if rows:
        body += _rows_table(rows_heading, rows)
    if button:
        body += _button(*button)
    extra = _features(features[0], features[1], numbered=features[2]) if features else ""
    links = " &nbsp;&middot;&nbsp; ".join(
        f'<a href="{html.escape(_link(settings, path))}" style="color:#475569;text-decoration:none">{label}</a>'
        for label, path in (("Pricing", "/pricing"), ("Tester guide", "/guide"), ("Dashboard", "/dashboard"))
    )
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        f'<body style="margin:0;padding:0;background:#eef1f7;font-family:{_FONT};color:#0f172a">'
        f'<div style="display:none;max-height:0;overflow:hidden;opacity:0">{html.escape(intro)}</div>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#eef1f7">'
        '<tr><td align="center" style="padding:32px 12px">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;'
        'background:#ffffff;border-radius:16px;overflow:hidden;border:1px solid #e2e8f0">'
        '<tr><td height="5" bgcolor="#ffd447" style="height:5px;line-height:5px;font-size:0;'
        f'background:{_GAUGE}">&nbsp;</td></tr>'
        f'<tr><td align="center" style="padding:28px 36px 24px"><img src="cid:{_LOGO_CID}" width="190" '
        'alt="Baseline11" style="display:block;width:190px;max-width:60%;height:auto;border:0"></td></tr>'
        f'<tr><td bgcolor="{_NAVY}" style="padding:34px 36px 32px;background:{_NAVY};'
        f'background-image:linear-gradient(135deg,{_NAVY} 0%,{_BLUE} 100%)">'
        '<p style="margin:0 0 10px;font-size:12px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;'
        f'color:#a9d0ff">{html.escape(eyebrow)}</p>'
        '<h1 style="margin:0 0 10px;font-size:26px;line-height:1.25;font-weight:800;color:#ffffff">'
        f"{html.escape(title)}</h1>"
        f'<p style="margin:0;font-size:15px;line-height:1.55;color:#dce9ff">{html.escape(intro)}</p></td></tr>'
        f'<tr><td style="padding:30px 36px 28px">{body}</td></tr>{extra}'
        "</table>"
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px">'
        f'<tr><td align="center" style="padding:22px 24px 8px;font-size:13px">{links}</td></tr>'
        '<tr><td align="center" style="padding:4px 24px;font-size:13px;color:#64748b">'
        "Postman collections in, correlated JMeter test plans out.</td></tr>"
        '<tr><td align="center" style="padding:10px 24px 0;font-size:12px;line-height:1.5;color:#94a3b8">'
        "You received this email because of your Baseline Auto Correlation account.<br>"
        f"&copy; {datetime.now(tz=UTC).year} Baseline11</td></tr></table>"
        "</td></tr></table></body></html>"
    )


def _text(paragraphs: list[str], *, rows: list[tuple[str, str]] | None = None, link: str | None = None) -> str:
    parts = list(paragraphs)
    if rows:
        parts.append("\n".join(f"{k}: {v}" for k, v in rows))
    if link:
        parts.append(link)
    parts.append("Baseline Auto Correlation")
    return "\n\n".join(parts) + "\n"


@cache
def _logo_png() -> bytes:
    return _LOGO_PATH.read_bytes()


def _message(settings: Settings, to: User, subject: str, text: str, html_body: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender(settings)
    message["To"] = to.email
    message.set_content(text)
    message.add_alternative(html_body, subtype="html")
    html_part = message.get_body(preferencelist=("html",))
    assert html_part is not None  # just added above
    html_part.add_related(
        _logo_png(), maintype="image", subtype="png", cid=f"<{_LOGO_CID}>",
        disposition="inline", filename="baseline11-logo.png",
    )
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
    start_url = _link(settings, "/")
    pricing_url = _link(settings, "/pricing")
    monthly = get_plan("monthly", settings)
    intro = "Your account is ready. Turn Postman collections into correlated JMeter test plans in minutes."
    free = f"{settings.free_uses} analyses with files up to {settings.free_max_file_mb} MB"
    upgrade = (f"Paid plans start at {format_price(monthly.price_cents, monthly.currency)} and add larger "
               "files, unlimited analyses and saved history." if monthly else
               "Paid plans add larger files, unlimited analyses and saved history.")
    callout = (f"<strong>Your free plan:</strong> {free}. {upgrade} "
               f'<a href="{html.escape(pricing_url)}" style="color:{_BLUE};font-weight:600">Compare plans</a>')
    return _message(
        settings, user, "Welcome to Baseline Auto Correlation",
        _text([f"Hi {user.name},", intro, f"Your free plan: {free}. {upgrade}", f"Start here: {start_url}"],
              link=pricing_url),
        _html(settings, eyebrow="Welcome aboard", title=f"Welcome, {user.name}", intro=intro,
              paragraphs=[f"Hi {name},",
                          "Thanks for joining Baseline Auto Correlation. Your first analysis takes only a "
                          "couple of minutes, and no JMeter scripting is needed."],
              callout=callout, button=("Start your first analysis", start_url),
              features=("How it works", _STEPS, True)),
    )


def purchase_confirmation(settings: Settings, user: User, plan: Plan, sub: Subscription) -> EmailMessage:
    name = html.escape(user.name)
    url = _link(settings, "/dashboard")
    rows = [*_plan_rows(plan, sub), ("Amount", format_price(sub.price_cents, sub.currency)),
            ("Reference", sub.payment_ref)]
    starts_later = sub.starts_at > sub.purchased_at + 60
    when = (f"It starts on {format_date(sub.starts_at)}, when your current plan ends."
            if starts_later else "It is active now.")
    lines = [f"Thank you for purchasing the {plan.name} plan.", when]
    included = [
        (f"Files up to {plan.max_file_mb} MB", "Upload larger collections and environment files."),
        ("Unlimited analyses", "Run as many analyses as you need while your plan is active."),
        (f"History kept for {plan.retention_days} days",
         "Download each collection, JMX and manifest again from your dashboard."),
    ]
    return _message(
        settings, user, f"Your {plan.name} plan is confirmed",
        _text([f"Hi {user.name},", *lines], rows=rows, link=url),
        _html(settings, eyebrow="Payment received", title="Purchase confirmed",
              intro=f"Your {plan.name} plan is confirmed. {when}",
              paragraphs=[f"Hi {name},", f"{lines[0]} Here is your receipt."],
              rows=rows, rows_heading="Your receipt",
              button=("Open your dashboard", url), features=("What's included", included, False)),
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
        _html(settings, eyebrow="Plan reminder", title="Your plan ends soon", intro=lines[0],
              paragraphs=[f"Hi {name},", *lines], button=("Renew your plan", url)),
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
        _html(settings, eyebrow="Plan update", title="Your plan has ended", intro=lines[0],
              paragraphs=[f"Hi {name},", *lines], button=("Choose a plan", url)),
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
        _html(settings, eyebrow="Account security", title="Reset your password", intro=lines[0],
              paragraphs=[f"Hi {name},", *lines], button=("Choose a new password", url)),
    )
