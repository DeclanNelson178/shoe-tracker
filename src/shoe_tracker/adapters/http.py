"""Polite HTTP client used by every retailer adapter.

Realistic User-Agent, 1–2s jitter between requests, exponential backoff on
the retailer-side rate-limit signals (429, 403). After `max_retries` the
client raises `RateLimitedError`; the orchestration layer (`rotation map`)
catches that, marks the retailer as skipped for this run, and moves on
without crashing the whole pipeline.
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

# Status codes worth backing off on. 429 is the polite signal; 403 is what
# Cloudflare-fronted retailers tend to return when they want us to slow down.
DEFAULT_RETRY_STATUS: tuple[int, ...] = (429, 403)


class RateLimitedError(Exception):
    """Raised when a retailer keeps rate-limiting us past `max_retries`."""


class HttpClient(Protocol):
    def get(self, url: str) -> str: ...


class PoliteClient:
    """httpx-backed client that sleeps between requests and retries on 429/403."""

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
        max_retries: int = 3,
        backoff_base_s: float = 2.0,
        retry_status: tuple[int, ...] = DEFAULT_RETRY_STATUS,
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
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self.retry_status = tuple(retry_status)

    def get(self, url: str) -> str:
        for attempt in range(self.max_retries + 1):
            self._wait_if_needed()
            resp = self._client.get(url)
            self._last_request_at = time.monotonic()
            if resp.status_code in self.retry_status:
                if attempt < self.max_retries:
                    self._sleep(self.backoff_base_s * (2 ** attempt))
                    continue
                raise RateLimitedError(
                    f"{url} returned HTTP {resp.status_code} after {attempt + 1} attempts"
                )
            resp.raise_for_status()
            return resp.text
        raise RuntimeError("unreachable")  # pragma: no cover

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
