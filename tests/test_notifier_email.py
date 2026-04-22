"""EmailNotifier tests.

The notifier takes a (user, TriggeredAlert) and produces a multipart email:
plain-text fallback + HTML body with shoe name, variant, price, retailer,
threshold delta, direct link, and an image thumbnail when available.

We stub SMTP so tests never hit the network.
"""
from __future__ import annotations

import pytest

from shoe_tracker.evaluator import TriggeredAlert
from shoe_tracker.models import (
    CanonicalShoe,
    ShoeVariant,
    User,
    WatchlistEntry,
)
from shoe_tracker.notifiers import EmailNotifier, Notifier


class _StubSMTP:
    """Captures login + send_message calls; no network. Supports context manager."""

    def __init__(self, host: str, port: int, *, fail_send: bool = False):
        self.host = host
        self.port = port
        self.login_args: tuple[str, str] | None = None
        self.sent = []
        self._fail_send = fail_send

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def login(self, username: str, password: str) -> None:
        self.login_args = (username, password)

    def send_message(self, msg):
        if self._fail_send:
            raise RuntimeError("smtp boom")
        self.sent.append(msg)


@pytest.fixture
def user():
    return User(id="me", email="me@example.com")


@pytest.fixture
def alert():
    shoe = CanonicalShoe(
        id=1, brand="ASICS", model="Novablast", version="5", gender="mens",
    )
    variant = ShoeVariant(
        id=10, canonical_shoe_id=1, size=10.5, width="D",
        colorway_name="Black/Mint",
        image_url="https://cdn.example.com/novablast5-black-mint.jpg",
    )
    entry = WatchlistEntry(
        id=1, canonical_shoe_id=1, size=10.5, width="D", threshold_usd=100.0,
    )
    return TriggeredAlert(
        entry=entry, shoe=shoe, variant=variant,
        retailer="running_warehouse", price_usd=89.00,
        source_url="https://rw.example.com/novablast5?variant=10",
    )


def _make_notifier(captured: list[_StubSMTP], *, fail: bool = False) -> EmailNotifier:
    def factory(host, port):
        stub = _StubSMTP(host, port, fail_send=fail)
        captured.append(stub)
        return stub
    return EmailNotifier(
        host="smtp.example.com", port=465,
        username="bot@example.com", password="hunter2",
        from_addr="bot@example.com",
        smtp_factory=factory,
    )


def test_email_notifier_implements_abstract(user, alert):
    captured = []
    n = _make_notifier(captured)
    assert isinstance(n, Notifier)
    assert n.channel == "email"


def test_email_notifier_sends_and_returns_true(user, alert):
    captured: list[_StubSMTP] = []
    n = _make_notifier(captured)
    assert n.notify(user, alert) is True
    assert len(captured) == 1
    smtp = captured[0]
    assert smtp.login_args == ("bot@example.com", "hunter2")
    assert len(smtp.sent) == 1


def test_email_notifier_subject_has_shoe_and_price(user, alert):
    captured: list[_StubSMTP] = []
    n = _make_notifier(captured)
    n.notify(user, alert)
    msg = captured[0].sent[0]
    subject = msg["Subject"]
    assert "ASICS Novablast 5" in subject
    assert "89" in subject
    assert msg["To"] == "me@example.com"
    assert msg["From"] == "bot@example.com"


def test_email_notifier_body_includes_variant_link_image_delta(user, alert):
    captured: list[_StubSMTP] = []
    n = _make_notifier(captured)
    n.notify(user, alert)
    msg = captured[0].sent[0]

    # Plain-text part
    plain = None
    html = None
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype == "text/plain" and plain is None:
            plain = part.get_content()
        elif ctype == "text/html" and html is None:
            html = part.get_content()
    assert plain is not None, "plain-text fallback required"
    assert html is not None, "html body required"

    # Plain text must carry the essentials for terminal readers.
    assert "Novablast 5" in plain
    assert "Black/Mint" in plain
    assert "10.5" in plain
    assert "running_warehouse" in plain
    assert "89.00" in plain
    assert "100" in plain  # threshold
    assert "11" in plain   # savings $11
    assert "https://rw.example.com/novablast5?variant=10" in plain

    # HTML must include the link + thumbnail + delta.
    assert 'href="https://rw.example.com/novablast5?variant=10"' in html
    assert 'src="https://cdn.example.com/novablast5-black-mint.jpg"' in html
    assert "Black/Mint" in html
    assert "$89.00" in html
    assert "$11.00" in html  # formatted delta


def test_email_notifier_returns_false_when_send_fails(user, alert):
    captured: list[_StubSMTP] = []
    n = _make_notifier(captured, fail=True)
    assert n.notify(user, alert) is False


def test_email_notifier_handles_missing_image(user, alert):
    # Variant without image_url → html still renders, without <img>.
    shoe = alert.shoe
    variant = alert.variant.model_copy(update={"image_url": None})
    new_alert = TriggeredAlert(
        entry=alert.entry, shoe=shoe, variant=variant,
        retailer=alert.retailer, price_usd=alert.price_usd,
        source_url=alert.source_url,
    )
    captured: list[_StubSMTP] = []
    n = _make_notifier(captured)
    assert n.notify(user, new_alert) is True
    msg = captured[0].sent[0]
    html = next(
        part.get_content() for part in msg.walk()
        if part.get_content_type() == "text/html"
    )
    assert "<img" not in html


def test_email_notifier_from_env_reads_gmail_vars(monkeypatch):
    from shoe_tracker.notifiers import email_notifier_from_env

    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-password")
    monkeypatch.setenv("GMAIL_FROM", "shoe.bot@example.com")
    monkeypatch.setenv("NOTIFY_EMAIL", "me@example.com")

    n = email_notifier_from_env()
    assert n is not None
    assert n.from_addr == "shoe.bot@example.com"
    assert n.username == "shoe.bot@example.com"
    assert n.password == "app-password"
    assert n.host == "smtp.gmail.com"
    assert n.port == 465


def test_email_notifier_from_env_returns_none_when_unconfigured(monkeypatch):
    from shoe_tracker.notifiers import email_notifier_from_env

    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    monkeypatch.delenv("GMAIL_FROM", raising=False)
    assert email_notifier_from_env() is None
