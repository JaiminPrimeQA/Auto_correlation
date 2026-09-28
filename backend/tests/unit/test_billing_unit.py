"""Plans, the account store, maintenance (reminders, expiry notices, purge),
email content and SMTP delivery."""

import smtplib
from datetime import UTC, datetime

import pytest

from app.core.config import Settings
from app.core.errors import ProblemException
from app.core.passwords import hash_password, verify_password
from app.domain.plans import add_months, format_price, get_plan
from app.repositories.account_store import AccountStore, EmailTaken, Subscription
from app.services import billing, emails
from app.services.mailer import BackgroundMailer, LogMailer, RecordingMailer, SmtpMailer, build_mailer
from app.services.maintenance import run_maintenance

DAY = 86400
NOW = datetime(2026, 1, 31, 12, tzinfo=UTC).timestamp()


@pytest.fixture
def store(tmp_path):
    return AccountStore(":memory:", str(tmp_path / "history"))


@pytest.fixture
def settings():
    return Settings()


def _user(store, email="ada@example.com"):
    return store.create_user(email=email, name="Ada", password_hash="x", now=NOW)


# --- plans ---


def test_add_months_clamps_to_the_end_of_shorter_months():
    feb = datetime.fromtimestamp(add_months(NOW, 1), tz=UTC)
    assert (feb.year, feb.month, feb.day, feb.hour) == (2026, 2, 28, 12)
    jul = datetime.fromtimestamp(add_months(NOW, 6), tz=UTC)
    assert (jul.month, jul.day) == (7, 31)
    nxt = datetime.fromtimestamp(add_months(NOW, 12), tz=UTC)
    assert (nxt.year, nxt.month, nxt.day) == (2027, 1, 31)


def test_format_price():
    assert format_price(900, "USD") == "$9.00"
    assert format_price(449900, "INR") == "₹4,499.00"
    assert format_price(500, "CHF") == "5.00 CHF"


def test_passwords_hash_and_verify():
    stored = hash_password("s3cret-pass")
    assert stored.startswith("scrypt$") and "s3cret" not in stored
    assert verify_password("s3cret-pass", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("x", "garbage")


# --- store ---


def test_emails_are_unique_case_insensitively(store):
    _user(store)
    with pytest.raises(EmailTaken):
        _user(store, "ADA@example.com")


def test_sessions_expire_and_only_their_hash_is_stored(store):
    user = _user(store)
    token = store.create_session(user.id, now=NOW, ttl_seconds=60)
    assert store.session_user(token, now=NOW + 30).id == user.id
    assert store.session_user(token, now=NOW + 61) is None
    rows = store._all("SELECT token_hash FROM sessions")
    assert rows and all(r["token_hash"] != token for r in rows)


def test_only_the_newest_reset_link_works_once(store):
    user = _user(store)
    old = store.create_password_reset(user.id, now=NOW, ttl_seconds=600)
    new = store.create_password_reset(user.id, now=NOW, ttl_seconds=600)
    assert store.consume_password_reset(old, now=NOW) is None
    assert store.consume_password_reset(new, now=NOW) == user.id
    assert store.consume_password_reset(new, now=NOW) is None


def test_expired_reset_link_is_refused(store):
    user = _user(store)
    token = store.create_password_reset(user.id, now=NOW, ttl_seconds=600)
    assert store.consume_password_reset(token, now=NOW + 601) is None


def test_free_uses_are_counted_against_every_key(store):
    assert store.consume_free_use(["device:a", "ip:1"], limit=2, now=NOW)
    assert store.consume_free_use(["device:b", "ip:1"], limit=2, now=NOW)
    # The IP is at the limit, so a new browser on it is refused.
    assert not store.consume_free_use(["device:c", "ip:1"], limit=2, now=NOW)
    assert store.free_uses_used(["device:c"]) == 0
    store.refund_free_use(["device:b", "ip:1"], now=NOW)
    assert store.free_uses_used(["ip:1"]) == 1


def test_entitlement_without_a_plan_uses_the_free_limits(store, settings):
    ent = billing.resolve_entitlement(store, settings, user=None, device_id="d", client_ip="1.2.3.4", now=NOW)
    assert not ent.paid and ent.max_file_bytes == 2 * 1024 * 1024 and ent.uses_remaining == 3
    assert ent.usage_keys == ("device:d",)


def test_ip_counting_can_be_turned_on(store):
    ent = billing.resolve_entitlement(
        store, Settings(free_tier_count_by_ip=True), user=None, device_id="d", client_ip="1.2.3.4", now=NOW,
    )
    assert ent.usage_keys == ("device:d", "ip:1.2.3.4")


def test_signed_out_callers_must_sign_in_when_accounts_are_required(store):
    settings = Settings(require_account=True)
    ent = billing.resolve_entitlement(store, settings, user=None, device_id="d", client_ip=None, now=NOW)
    assert ent.sign_in_required
    with pytest.raises(ProblemException) as info:
        billing.ensure_can_start(ent)
    assert info.value.status == 401 and info.value.code == "sign_in_required"

    signed_in = billing.resolve_entitlement(store, settings, user=_user(store), device_id="d", client_ip=None, now=NOW)
    assert not signed_in.sign_in_required
    billing.ensure_can_start(signed_in)


def test_oidc_mode_does_not_ask_for_an_account(store):
    settings = Settings(require_account=True, auth_mode="oidc")
    ent = billing.resolve_entitlement(store, settings, user=None, device_id="d", client_ip=None, now=NOW)
    assert not ent.sign_in_required


def test_a_plan_bought_now_extends_existing_history(store, settings):
    user = _user(store)
    entry = store.create_history(user_id=user.id, plan="monthly", collection_name="c", now=NOW,
                                 retention_seconds=7 * DAY)
    billing.purchase(store, settings, user, "yearly", now=NOW + DAY)
    assert store.get_history(entry.id).expires_at == NOW + 90 * DAY


def test_purchases_are_refused_when_billing_is_disabled(store):
    user = _user(store)
    with pytest.raises(billing.PurchaseError):
        billing.purchase(store, Settings(billing_provider="disabled"), user, "monthly", now=NOW)


# --- maintenance ---


def _sub(store, user, *, plan="monthly", starts_at, expires_at, sub_id="s1", price_cents=900):
    sub = Subscription(id=sub_id, user_id=user.id, plan=plan, price_cents=price_cents, currency="USD",
                       payment_provider="demo", payment_ref="demo_1", purchased_at=starts_at,
                       starts_at=starts_at, expires_at=expires_at)
    store.add_subscription(sub)
    return sub


def test_reminder_is_sent_once_within_the_window(store, settings):
    user = _user(store)
    _sub(store, user, starts_at=NOW - 20 * DAY, expires_at=NOW + 10 * DAY)
    mailer = RecordingMailer()
    assert run_maintenance(store, mailer, settings, now=NOW).reminders == 0
    assert run_maintenance(store, mailer, settings, now=NOW + 7.5 * DAY).reminders == 1
    assert run_maintenance(store, mailer, settings, now=NOW + 8 * DAY).reminders == 0
    [reminder] = mailer.sent
    assert reminder["Subject"] == "Your Monthly plan ends in 2 days"
    assert reminder["To"] == "ada@example.com"


def test_a_renewed_plan_gets_no_reminder_or_expiry_notice(store, settings):
    user = _user(store)
    first = _sub(store, user, starts_at=NOW - 20 * DAY, expires_at=NOW + DAY)
    _sub(store, user, plan="yearly", starts_at=first.expires_at, expires_at=first.expires_at + 365 * DAY,
         sub_id="s2")
    mailer = RecordingMailer()
    result = run_maintenance(store, mailer, settings, now=NOW + 2 * DAY)
    assert (result.reminders, result.expiry_notices) == (0, 0)
    assert mailer.sent == []


def test_expiry_notice_is_sent_once(store, settings):
    user = _user(store)
    _sub(store, user, starts_at=NOW - 30 * DAY, expires_at=NOW - 60)
    mailer = RecordingMailer()
    assert run_maintenance(store, mailer, settings, now=NOW).expiry_notices == 1
    assert run_maintenance(store, mailer, settings, now=NOW + DAY).expiry_notices == 0
    assert mailer.sent[0]["Subject"] == "Your Monthly plan has ended"


def test_long_expired_plans_get_no_late_notice(store, settings):
    user = _user(store)
    _sub(store, user, starts_at=NOW - 60 * DAY, expires_at=NOW - 30 * DAY)
    mailer = RecordingMailer()
    assert run_maintenance(store, mailer, settings, now=NOW).expiry_notices == 0
    assert mailer.sent == []


def test_maintenance_purges_expired_history_and_tokens(store, settings):
    user = _user(store)
    kept = store.create_history(user_id=user.id, plan="monthly", collection_name="k", now=NOW,
                                retention_seconds=7 * DAY, collection_raw=b"{}")
    gone = store.create_history(user_id=user.id, plan="monthly", collection_name="g", now=NOW - 8 * DAY,
                                retention_seconds=7 * DAY, collection_raw=b"{}")
    store.create_session(user.id, now=NOW - 2 * DAY, ttl_seconds=DAY)
    result = run_maintenance(store, RecordingMailer(), settings, now=NOW)
    assert (result.history_removed, result.tokens_removed) == (1, 1)
    assert store.get_history(gone.id) is None and not store.entry_dir(gone.id).exists()
    assert store.read_history_file(kept.id, "collection") == b"{}"


# --- emails and delivery ---


def test_purchase_email_lists_the_plan_and_escapes_the_name(store, settings):
    user = store.create_user(email="x@example.com", name="<b>Eve</b>", password_hash="x", now=NOW)
    plan = get_plan("yearly", settings)
    sub = _sub(store, user, plan="yearly", starts_at=NOW, expires_at=add_months(NOW, 12), price_cents=7900)
    message = emails.purchase_confirmation(Settings(mail_from_address="billing@example.com"), user, plan, sub)
    assert message["From"] == "Baseline Auto Correlation <billing@example.com>"
    html_part = message.get_body(preferencelist=("html",)).get_content()
    text_part = message.get_body(preferencelist=("plain",)).get_content()
    assert "&lt;b&gt;Eve&lt;/b&gt;" in html_part and "<b>Eve</b>" not in html_part
    for fact in ("Yearly", "31 Jan 2027", "200 MB per file", "90 days", "$79.00"):
        assert fact in text_part


class _FakeSmtp:
    instances: list = []

    def __init__(self, host, port, timeout=None, **kwargs):
        self.host, self.port, self.calls = host, port, []
        _FakeSmtp.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, username, password):
        self.calls.append(("login", username, password))

    def send_message(self, message):
        self.calls.append(("send", message["To"]))


def test_smtp_mailer_uses_starttls_and_logs_in(monkeypatch, store):
    _FakeSmtp.instances.clear()
    monkeypatch.setattr(smtplib, "SMTP", _FakeSmtp)
    settings = Settings(smtp_host="smtp.gmail.com", smtp_port=587, smtp_username="me@gmail.com",
                        smtp_password="app-password")
    SmtpMailer(settings).send(emails.welcome(settings, _user(store)))
    [server] = _FakeSmtp.instances
    assert (server.host, server.port) == ("smtp.gmail.com", 587)
    assert server.calls == ["starttls", ("login", "me@gmail.com", "app-password"), ("send", "ada@example.com")]


def test_background_mailer_swallows_delivery_failures():
    class Broken:
        def send(self, message):
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    mailer = BackgroundMailer(Broken())
    from email.message import EmailMessage

    message = EmailMessage()
    message["Subject"] = "x"
    mailer.send(message)  # never raises
    mailer._pool.shutdown(wait=True)


def test_without_smtp_host_emails_are_only_logged():
    mailer = build_mailer(Settings(smtp_host=None))
    assert isinstance(mailer._inner, LogMailer)
