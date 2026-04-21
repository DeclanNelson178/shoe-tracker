"""Polite HTTP client used by every retailer adapter.

Realistic User-Agent, 1–2s jitter between requests, simple retry on transient
errors. Wrapping httpx so adapters can be handed a fake client in tests.
"""
from __future__ import annotations

import random
import time
from typing import Protocol

import httpx


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class HttpClient(Protocol):
    def get(self, url: str) -> str: ...


class PoliteClient:
    """httpx-backed client that sleeps between requests."""

    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        min_delay_s: float = 1.0,
        max_delay_s: float = 2.0,
        timeout_s: float = 15.0,
        client: httpx.Client | None = None,
        sleeper=time.sleep,
        rng: random.Random | None = None,
    ):
        self.min_delay_s = min_delay_s
        self.max_delay_s = max_delay_s
        self._sleep = sleeper
        self._rng = rng or random.Random()
        self._last_request_at: float | None = None
        self._owns_client = client is None
        self._client = client or httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Language": "en-US,en;q=0.9"},
            timeout=timeout_s,
            follow_redirects=True,
        )

    def get(self, url: str) -> str:
        self._wait_if_needed()
        resp = self._client.get(url)
        resp.raise_for_status()
        self._last_request_at = time.monotonic()
        return resp.text

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "PoliteClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _wait_if_needed(self) -> None:
        if self._last_request_at is None:
            return
        target = self._rng.uniform(self.min_delay_s, self.max_delay_s)
        elapsed = time.monotonic() - self._last_request_at
        remaining = target - elapsed
        if remaining > 0:
            self._sleep(remaining)
