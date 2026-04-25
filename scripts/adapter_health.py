"""Probe every retailer adapter with a known-good canonical shoe.

Catches silent breakage: if a retailer changes their HTML and our parser
starts returning empty lists instead of crashing, we'd never know without
this. Exits 0 when every adapter returns at least one search result, exits
1 (and lists the failures) otherwise.

Run weekly via .github/workflows/health.yml. The workflow's `if: failure()`
step calls notify_workflow_failure.py — same email path the daily scrape
uses.
"""
from __future__ import annotations

from dataclasses import dataclass

from shoe_tracker.adapters import ADAPTERS
from shoe_tracker.adapters.base import RetailerAdapter
from shoe_tracker.models import CanonicalShoe

CHECK_SHOE = CanonicalShoe(
    brand="ASICS", model="Novablast", version="5", gender="mens",
)


@dataclass(frozen=True)
class ProbeResult:
    retailer: str
    ok: bool
    count: int
    error: str | None = None

    @property
    def status_label(self) -> str:
        if self.ok:
            return f"PASS ({self.count} results)"
        if self.error:
            return f"FAIL: {self.error}"
        return "FAIL: 0 results"


def probe_all(adapters=None) -> list[ProbeResult]:
    """Run search() against each adapter; collect pass/fail summaries."""
    adapters = ADAPTERS if adapters is None else adapters
    return [_probe_one(name, factory()) for name, factory in sorted(adapters.items())]


def _probe_one(name: str, adapter: RetailerAdapter) -> ProbeResult:
    try:
        results = adapter.search(CHECK_SHOE)
    except Exception as exc:
        return ProbeResult(
            retailer=name, ok=False, count=0,
            error=f"{type(exc).__name__}: {exc}",
        )
    count = len(results)
    return ProbeResult(retailer=name, ok=count > 0, count=count)


def main() -> int:
    results = probe_all()
    print("Adapter health probe:")
    for r in results:
        marker = "PASS" if r.ok else "FAIL"
        print(f"  [{marker}] {r.retailer:<24} {r.status_label}")
    failures = [r for r in results if not r.ok]
    if failures:
        names = ", ".join(r.retailer for r in failures)
        print(f"\n{len(failures)} adapter(s) failed: {names}")
        return 1
    print(f"\nAll {len(results)} adapter(s) healthy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
