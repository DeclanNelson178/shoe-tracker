"""Notification channels.

The Notifier abstract base class is the v1→v2 carry-over contract: v1 ships
with `EmailNotifier`; v2 can add Pushover/ntfy/etc. without touching the
evaluator.
"""
from __future__ import annotations

from .base import Notifier
from .email import EmailNotifier, email_notifier_from_env

__all__ = ["EmailNotifier", "Notifier", "email_notifier_from_env"]
