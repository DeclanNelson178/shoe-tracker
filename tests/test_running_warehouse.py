"""Running Warehouse adapter tests.

Unit tests parse fixtures under `tests/fixtures/running_warehouse/`. The
`@pytest.mark.live` test at the bottom hits the real site — skipped unless
`SHOE_TRACKER_LIVE=1` is set.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from shoe_tracker.adapters import RunningWarehouseAdapter
from shoe_tracker.adapters.base import VariantPrice
from shoe_tracker.adapters.running_warehouse import (
    BASE_URL,
    parse_product_page,
    parse_search_results,
)
from shoe_tracker.models import CanonicalShoe

FIXTURES = Path(__file__).parent / "fixtures" / "running_warehouse"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text()


class _FakeClient:
    """In-memory HttpClient — tests inject captured HTML, no network."""

    def __init__(self, responses: dict[str, str]):
        self._responses = responses
        self.calls: list[str] = []

    def get(self, url: str) -> str:
        self.calls.append(url)
        try:
            return self._responses[url]
        except KeyError as e:
            raise AssertionError(f"unexpected URL fetched: {url}") from e


# --- search parser ---

def test_parse_search_results_returns_one_entry_per_colorway():
    html = _load("search_mens_novablast.html")
    results = parse_search_results(html)
    # Site's search is fuzzy — both "Novablast 5" and "Novablast 5 GS" (kids) match.
    # Canonical-matching is the mapping engine's job (chunk 3); the adapter
    # just returns every card.
    assert len(results) >= 6, f"expected multiple colorway cards, got {len(results)}"
    assert any(r.title == "ASICS Novablast 5" for r in results), [r.title for r in results]
    # URLs should be absolute
    assert all(r.product_url.startswith("https://www.runningwarehouse.com/") for r in results)
    assert all("descpage-" in r.product_url for r in results)
    # Colorway names populated, price hints populated
    assert all(r.colorway_name for r in results)
    assert all(r.price_hint_usd and r.price_hint_usd > 0 for r in results)


def test_parse_search_results_extracts_product_code_from_url():
    html = _load("search_mens_novablast.html")
    results = parse_search_results(html)
    codes = {r.product_code for r in results}
    # Different SKU families exist for the same shoe — prove we captured several.
    assert any(c and c.startswith("ANB5M") for c in codes)


# --- product-page parser ---

def test_parse_product_page_returns_one_variant_per_size_width():
    html = _load("product_anb5m1.html")
    variants = parse_product_page(html, source_url="https://www.runningwarehouse.com/descpage-ANB5M1.html")
    # ANB5M1 is the Gravel/White mens colorway: 14 D-width + 9 2E-width sizes = 23 rows
    assert len(variants) == 23
    assert all(isinstance(v, VariantPrice) for v in variants)
    assert all(v.retailer == "running_warehouse" for v in variants)
    assert all(v.price_usd == 149.95 for v in variants)
    assert all(v.in_stock for v in variants), "all rows in the fixture show In Stock > 0"
    assert all(v.colorway_name == "Gravel/White" for v in variants)
    assert {v.width for v in variants} == {"D", "2E"}
    # Sizes include 10.5 in both widths
    d_size_105 = next((v for v in variants if v.size == 10.5 and v.width == "D"), None)
    assert d_size_105 is not None
    assert d_size_105.colorway_code == "ANB5M1"
    assert d_size_105.mfr_style_code == "1011B974.020"
    assert d_size_105.image_url and "img.runningwarehouse.com" in d_size_105.image_url


def test_parse_product_page_different_colorway():
    html = _load("product_anb5m3.html")
    variants = parse_product_page(html, source_url="https://x/descpage-ANB5M3.html")
    assert variants, "expected variants for a second colorway fixture"
    assert {v.colorway_name for v in variants} == {"Cold Moss/Light Orange"}
    assert {v.colorway_code for v in variants} == {"ANB5M3"}


def test_parse_product_page_detects_out_of_stock_rows():
    html = _load("product_oos_synthetic.html")
    variants = parse_product_page(
        html, source_url="https://www.runningwarehouse.com/descpage-ANB5M9.html",
    )
    # Fixture defines 3 rows: 10.0 in stock, 10.5 explicit 0, 11.0 notify-me only.
    assert len(variants) == 3
    by_size = {v.size: v for v in variants}
    assert by_size[10.0].in_stock is True
    assert by_size[10.5].in_stock is False, "stock count 0 must map to in_stock=False"
    assert by_size[11.0].in_stock is False, "notify-me row without stock count is OOS"
    # The OOS rows still carry price + colorway, so the evaluator can reason about them.
    assert by_size[10.5].price_usd == 74.95
    assert all(v.colorway_name == "Safety Yellow" for v in variants)


# --- adapter wiring ---

def test_adapter_fetches_and_parses_via_injected_client():
    html = _load("product_anb5m1.html")
    url = "https://www.runningwarehouse.com/ASICS_Novablast_5/descpage-ANB5M1.html"
    client = _FakeClient({url: html})
    adapter = RunningWarehouseAdapter(client=client)
    variants = adapter.fetch_variants(url)
    assert variants
    assert client.calls == [url]
    assert all(v.product_url == url for v in variants)


def test_adapter_search_builds_mens_search_url():
    html = _load("search_mens_novablast.html")
    canonical = CanonicalShoe(brand="ASICS", model="Novablast", version="5", gender="mens")
    captured: dict[str, str] = {
        f"{BASE_URL}/search-mens.html?searchtext=ASICS+Novablast+5": html,
    }
    client = _FakeClient(captured)
    adapter = RunningWarehouseAdapter(client=client)
    results = adapter.search(canonical)
    assert len(client.calls) == 1
    assert "search-mens.html" in client.calls[0]
    assert "searchtext=ASICS+Novablast+5" in client.calls[0]
    assert results
    assert all(r.retailer == "running_warehouse" for r in results)


def test_adapter_search_switches_to_womens_for_womens_canonical():
    html = _load("search_mens_novablast.html")  # reused; URL is what's asserted
    canonical = CanonicalShoe(brand="ASICS", model="Novablast", version="5", gender="womens")
    client = _FakeClient({
        f"{BASE_URL}/search-womens.html?searchtext=ASICS+Novablast+5": html,
    })
    RunningWarehouseAdapter(client=client).search(canonical)
    assert "search-womens.html" in client.calls[0]


def test_adapter_advertises_metadata():
    adapter = RunningWarehouseAdapter(client=_FakeClient({}))
    assert adapter.name == "running_warehouse"
    assert adapter.requires_js is False
    assert adapter.supports_style_codes is True


# --- live (env-gated) ---

@pytest.mark.live
@pytest.mark.skipif(os.getenv("SHOE_TRACKER_LIVE") != "1", reason="set SHOE_TRACKER_LIVE=1 to run")
def test_live_search_and_fetch_novablast_5():
    adapter = RunningWarehouseAdapter()
    canonical = CanonicalShoe(brand="ASICS", model="Novablast", version="5", gender="mens")
    results = adapter.search(canonical)
    assert results, "RW search should return at least one colorway card"
    first = results[0]
    variants = adapter.fetch_variants(first.product_url)
    assert variants, f"no variants parsed from {first.product_url}"
    assert any(v.in_stock for v in variants), "expected at least one in-stock variant"
