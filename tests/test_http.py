"""Tests for the polite HTTP client + rate-limit backoff.

The retry path is the only thing here that's non-trivial; the rest of
PoliteClient (User-Agent, jitter, ownership of the underlying httpx client)
is exercised by every adapter test.
"""
from __future__ import annotations

import httpx
import pytest

from shoe_tracker.adapters.http import PoliteClient, RateLimitedError


class _FakeHttpx:
    """Stand-in for httpx.Client. Returns responses by status code."""

    def __init__(self, status_codes, body: str = "ok") -> None:
        self._codes = list(status_codes)
        self._body = body
        self.calls: list[str] = []

    def get(self, url: str) -> httpx.Response:
        self.calls.append(url)
        code = self._codes.pop(0)
        return httpx.Response(
            status_code=code,
            request=httpx.Request("GET", url),
            text=self._body,
        )


def _make(status_codes, sleeps=None):
    sleeps = sleeps if sleeps is not None else []
    fake = _FakeHttpx(status_codes)
    pc = PoliteClient(
        client=fake,
        min_delay_s=0, max_delay_s=0,
        sleeper=lambda s: sleeps.append(s),
        max_retries=3,
        backoff_base_s=2.0,
    )
    return pc, fake, sleeps


def test_returns_body_on_200():
    pc, _, _ = _make([200])
    assert pc.get("https://x") == "ok"


def test_retries_on_429_then_succeeds_with_exponential_backoff():
    pc, fake, sleeps = _make([429, 429, 200])
    assert pc.get("https://x") == "ok"
    assert fake.calls == ["https://x", "https://x", "https://x"]
    assert sleeps == [2.0, 4.0]  # 2 * 2^0, 2 * 2^1


def test_retries_on_403_too():
    pc, _, sleeps = _make([403, 200])
    pc.get("https://x")
    assert sleeps == [2.0]


def test_raises_rate_limited_after_max_retries():
    pc, fake, sleeps = _make([429, 429, 429, 429])
    with pytest.raises(RateLimitedError) as exc:
        pc.get("https://x")
    assert "429" in str(exc.value)
    # 3 backoffs (2, 4, 8) then the 4th attempt raises.
    assert sleeps == [2.0, 4.0, 8.0]
    assert len(fake.calls) == 4


def test_404_raises_immediately_without_retry():
    pc, fake, sleeps = _make([404])
    with pytest.raises(httpx.HTTPStatusError):
        pc.get("https://x")
    assert sleeps == []
    assert fake.calls == ["https://x"]


def test_500_raises_immediately_without_retry():
    pc, _, sleeps = _make([500])
    with pytest.raises(httpx.HTTPStatusError):
        pc.get("https://x")
    assert sleeps == []
