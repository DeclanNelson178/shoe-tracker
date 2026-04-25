"""Tests for the workflow-failure email helper.

The script runs in `.github/workflows/scrape.yml` as the `if: failure()` step.
It must work with stdlib only (the package install may itself have failed) and
be cleanly testable with an injected SMTP factory.
"""
from __future__ import annotations

import pytest
from notify_workflow_failure import build_message, main


class _RecordingSMTP:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.logged_in: tuple[str, str] | None = None
        self.sent = None

    def __enter__(self) -> "_RecordingSMTP":
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def login(self, user: str, password: str) -> None:
        self.logged_in = (user, password)

    def send_message(self, msg) -> None:
        self.sent = msg


def _factory():
    captured: list[_RecordingSMTP] = []

    def make(host: str, port: int) -> _RecordingSMTP:
        smtp = _RecordingSMTP(host, port)
        captured.append(smtp)
        return smtp

    return make, captured


def test_build_message_carries_run_url_workflow_and_repo():
    msg = build_message(
        from_addr="bot@example.com",
        to_addr="me@example.com",
        workflow="Scrape",
        repo="me/shoe-tracker",
        run_url="https://github.com/me/shoe-tracker/actions/runs/123",
    )
    assert msg["From"] == "bot@example.com"
    assert msg["To"] == "me@example.com"
    assert "Scrape" in msg["Subject"]
    body = msg.get_content()
    assert "https://github.com/me/shoe-tracker/actions/runs/123" in body
    assert "me/shoe-tracker" in body
    assert "Scrape" in body


def test_main_sends_email_when_creds_set():
    factory, captured = _factory()
    env = {
        "GMAIL_FROM": "bot@example.com",
        "GMAIL_APP_PASSWORD": "app-pw",
        "NOTIFY_EMAIL": "me@example.com",
        "GITHUB_WORKFLOW": "Scrape",
        "GITHUB_REPOSITORY": "me/shoe-tracker",
        "GITHUB_RUN_ID": "42",
        "GITHUB_SERVER_URL": "https://github.com",
    }
    code = main(env=env, smtp_factory=factory)
    assert code == 0
    assert len(captured) == 1
    smtp = captured[0]
    assert smtp.host == "smtp.gmail.com"
    assert smtp.port == 465
    assert smtp.logged_in == ("bot@example.com", "app-pw")
    assert smtp.sent is not None
    assert smtp.sent["To"] == "me@example.com"
    body = smtp.sent.get_content()
    assert "actions/runs/42" in body


def test_main_falls_back_to_gmail_from_when_notify_email_missing():
    factory, captured = _factory()
    env = {
        "GMAIL_FROM": "bot@example.com",
        "GMAIL_APP_PASSWORD": "x",
    }
    code = main(env=env, smtp_factory=factory)
    assert code == 0
    assert captured[0].sent["To"] == "bot@example.com"


def test_main_skips_silently_when_creds_missing(capsys):
    def _refuse(host, port):
        pytest.fail("SMTP must not be invoked without credentials")

    code = main(env={}, smtp_factory=_refuse)
    assert code == 0
    err = capsys.readouterr().err
    assert "missing" in err.lower()


def test_main_returns_nonzero_when_smtp_raises():
    def factory(host, port):
        raise RuntimeError("smtp down")

    code = main(
        env={
            "GMAIL_FROM": "bot@example.com",
            "GMAIL_APP_PASSWORD": "x",
            "NOTIFY_EMAIL": "me@example.com",
        },
        smtp_factory=factory,
    )
    assert code == 1
