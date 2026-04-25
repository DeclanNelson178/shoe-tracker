"""Tests for scripts/adapter_health.py.

The probe runs against every adapter once a week (live, hits real retailers).
These tests cover the orchestration logic offline using fake adapters.
"""
from __future__ import annotations

import adapter_health
from adapter_health import main, probe_all


class _PassAdapter:
    name = "pass"

    def search(self, canonical):
        return [object(), object()]

    def fetch_variants(self, url):
        return []


class _EmptyAdapter:
    name = "empty"

    def search(self, canonical):
        return []

    def fetch_variants(self, url):
        return []


class _RaiseAdapter:
    name = "raise"

    def search(self, canonical):
        raise RuntimeError("retailer site down")

    def fetch_variants(self, url):
        return []


def test_probe_all_passes_when_adapter_returns_results():
    results = probe_all({"pass": _PassAdapter})
    assert len(results) == 1
    r = results[0]
    assert r.retailer == "pass"
    assert r.ok is True
    assert r.count == 2
    assert r.error is None


def test_probe_all_fails_when_adapter_returns_empty():
    results = probe_all({"empty": _EmptyAdapter})
    r = results[0]
    assert r.ok is False
    assert r.count == 0


def test_probe_all_catches_exceptions_as_failure():
    results = probe_all({"raise": _RaiseAdapter})
    r = results[0]
    assert r.ok is False
    assert r.error is not None
    assert "RuntimeError" in r.error


def test_probe_all_runs_every_adapter_even_if_one_fails():
    results = probe_all({
        "alpha": _PassAdapter,
        "bravo": _RaiseAdapter,
        "charlie": _PassAdapter,
    })
    by_name = {r.retailer: r for r in results}
    assert by_name["alpha"].ok
    assert not by_name["bravo"].ok
    assert by_name["charlie"].ok


def test_main_exits_zero_when_all_healthy(monkeypatch, capsys):
    monkeypatch.setattr(adapter_health, "ADAPTERS", {"pass": _PassAdapter})
    code = main()
    assert code == 0
    out = capsys.readouterr().out
    assert "All 1 adapter" in out


def test_main_exits_one_when_any_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        adapter_health, "ADAPTERS",
        {"empty": _EmptyAdapter, "ok": _PassAdapter},
    )
    code = main()
    assert code == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "empty" in out
