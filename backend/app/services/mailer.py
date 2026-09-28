"""Outgoing email.

``SmtpMailer`` sends through the configured SMTP server; without one,
``LogMailer`` records that a message would have been sent (development).
``BackgroundMailer`` sends off the request thread so a slow mail server never
delays a response; a failure is logged and never reaches the caller.
"""

from __future__ import annotations

import smtplib
import ssl
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage
from email.utils import formataddr
from typing import Protocol

from ..core.config import Settings
from ..core.logging import get_logger

log = get_logger("mailer")


class Mailer(Protocol):
    def send(self, message: EmailMessage) -> None: ...


class SmtpMailer:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    def send(self, message: EmailMessage) -> None:
        s = self._s
        context = ssl.create_default_context()
        if s.smtp_security == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                str(s.smtp_host), s.smtp_port, timeout=s.smtp_timeout_seconds, context=context,
            )
        else:
            server = smtplib.SMTP(str(s.smtp_host), s.smtp_port, timeout=s.smtp_timeout_seconds)
        with server:
            if s.smtp_security == "starttls":
                server.starttls(context=context)
            if s.smtp_username and s.smtp_password:
                server.login(s.smtp_username, s.smtp_password)
            server.send_message(message)


class LogMailer:
    def send(self, message: EmailMessage) -> None:
        log.info(f"email not sent (SMTP is not configured): {message['Subject']}", extra={"stage": "email"})


class RecordingMailer:
    """Keeps sent messages in memory (tests)."""

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> None:
        self.sent.append(message)


class BackgroundMailer:
    def __init__(self, inner: Mailer, *, workers: int = 2) -> None:
        self._inner = inner
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mailer")

    def _deliver(self, message: EmailMessage) -> None:
        try:
            self._inner.send(message)
            log.info(f"email sent: {message['Subject']}", extra={"stage": "email"})
        except Exception as exc:  # noqa: BLE001 - delivery problems are logged, never raised
            log.error(
                f"email delivery failed: {message['Subject']}",
                extra={"stage": "email", "exc_type": type(exc).__name__},
            )

    def send(self, message: EmailMessage) -> None:
        self._pool.submit(self._deliver, message)


def build_mailer(settings: Settings) -> Mailer:
    inner: Mailer = SmtpMailer(settings) if settings.smtp_host else LogMailer()
    return BackgroundMailer(inner)


def sender(settings: Settings) -> str:
    address = settings.mail_from_address or settings.smtp_username or "no-reply@localhost"
    return formataddr((settings.mail_from_name, address))
