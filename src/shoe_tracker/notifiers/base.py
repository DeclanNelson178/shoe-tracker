"""Notifier interface.

v2 will add other channels (Pushover, ntfy). Keeping the interface narrow —
`notify(user, alert) -> bool` — means the evaluator doesn't need to know.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..evaluator import TriggeredAlert
from ..models import User


class Notifier(ABC):
    """One channel that can deliver a TriggeredAlert to a User."""

    channel: str

    @abstractmethod
    def notify(self, user: User, alert: TriggeredAlert) -> bool:
        """Deliver the alert. Return True iff it was accepted by the channel."""
