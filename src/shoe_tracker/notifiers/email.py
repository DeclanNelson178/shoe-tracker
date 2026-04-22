"""Gmail SMTP email notifier.

Gmail requires an app password (2FA must be on). The notifier is wired via env
vars in the CLI: `GMAIL_FROM`, `GMAIL_APP_PASSWORD`. The host/port default to
Gmail's implicit-TLS endpoint but can be overridden for tests.

HTML body includes the product link, the variant's image thumbnail, and the
delta from the user's threshold — the details I actually want when the email
lands on my phone.
"""
from __future__ import annotations

import html
import os
import smtplib
from email.message import EmailMessage
from typing import Callable, ContextManager

from ..evaluator import TriggeredAlert
from ..models import User
from .base import Notifier


# Type alias for the smtplib factory. Production uses `smtplib.SMTP_SSL`, tests
# inject a stub. The object must support login, send_message, and the context
# manager protocol.
SMTPFactory = Callable[[str, int], ContextManager]


class EmailNotifier(Notifier):
    channel = "email"

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        from_addr: str,
        smtp_factory: SMTPFactory | None = None,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.from_addr = from_addr
        self._smtp_factory: SMTPFactory = smtp_factory or smtplib.SMTP_SSL

    def notify(self, user: User, alert: TriggeredAlert) -> bool:
        msg = self._build_message(user, alert)
        try:
            with self._smtp_factory(self.host, self.port) as smtp:
                smtp.login(self.username, self.password)
                smtp.send_message(msg)
        except Exception:
            return False
        return True

    def _build_message(self, user: User, alert: TriggeredAlert) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = self.from_addr
        msg["To"] = user.email
        msg["Subject"] = _subject(alert)
        msg.set_content(_plain_body(alert))
        msg.add_alternative(_html_body(alert), subtype="html")
        return msg


def email_notifier_from_env() -> EmailNotifier | None:
    """Build an EmailNotifier from the env vars the GitHub Action exposes.

    Returns None if any required variable is missing — callers can use this to
    distinguish "notifier not configured" from "notifier broken".
    """
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    from_addr = os.environ.get("GMAIL_FROM")
    if not (app_password and from_addr):
        return None
    return EmailNotifier(
        host=os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        port=int(os.environ.get("SMTP_PORT", "465")),
        username=from_addr,
        password=app_password,
        from_addr=from_addr,
    )


# --- body construction ---

def _subject(alert: TriggeredAlert) -> str:
    return (
        f"{alert.shoe.display_name} — ${alert.price_usd:.2f} "
        f"({alert.variant.colorway_name}) @ {alert.retailer}"
    )


def _plain_body(alert: TriggeredAlert) -> str:
    return (
        f"{alert.shoe.display_name}\n"
        f"Size {_fmt_size(alert.variant.size)} {alert.variant.width} "
        f"— {alert.variant.colorway_name}\n"
        f"${alert.price_usd:.2f} at {alert.retailer} "
        f"(threshold ${alert.threshold_usd:.2f}, save ${alert.delta_usd:.2f})\n"
        f"{alert.source_url}\n"
    )


def _html_body(alert: TriggeredAlert) -> str:
    shoe = html.escape(alert.shoe.display_name)
    colorway = html.escape(alert.variant.colorway_name)
    retailer = html.escape(alert.retailer)
    size = _fmt_size(alert.variant.size)
    width = html.escape(alert.variant.width)
    url = html.escape(alert.source_url, quote=True)
    img = ""
    if alert.variant.image_url:
        img_url = html.escape(alert.variant.image_url, quote=True)
        img = (
            f'<p><a href="{url}">'
            f'<img src="{img_url}" alt="{colorway}" '
            f'style="max-width:240px;border-radius:6px;border:1px solid #ddd"></a></p>'
        )
    return (
        "<html><body style=\"font-family:system-ui,sans-serif;color:#222\">"
        f"<h2 style=\"margin-bottom:4px\">{shoe}</h2>"
        f"<p style=\"color:#555;margin-top:0\">Size {size} {width} — {colorway}</p>"
        f"{img}"
        "<table style=\"border-collapse:collapse\">"
        f"<tr><td><strong>Price</strong></td>"
        f"<td style=\"padding-left:12px\">${alert.price_usd:.2f}</td></tr>"
        f"<tr><td><strong>Threshold</strong></td>"
        f"<td style=\"padding-left:12px\">${alert.threshold_usd:.2f}</td></tr>"
        f"<tr><td><strong>You save</strong></td>"
        f"<td style=\"padding-left:12px\">${alert.delta_usd:.2f}</td></tr>"
        f"<tr><td><strong>Retailer</strong></td>"
        f"<td style=\"padding-left:12px\">{retailer}</td></tr>"
        "</table>"
        f'<p><a href="{url}" '
        'style="background:#111;color:#fff;padding:10px 16px;'
        'border-radius:6px;text-decoration:none;display:inline-block">'
        "View at retailer</a></p>"
        "</body></html>"
    )


def _fmt_size(size: float) -> str:
    return f"{size:g}"
