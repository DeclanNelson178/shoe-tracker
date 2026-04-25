"""Holabird Sports adapter tests.

Unit tests parse fixtures under `tests/fixtures/holabird/`. The
`@pytest.mark.live` test at the bottom hits the real site — skipped unless
`SHOE_TRACKER_LIVE=1` is set.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from shoe_tracker.adapters import HolabirdAdapter
from shoe_tracker.adapters.base import VariantPrice
from shoe_tracker.adapters.holabird import (
    BASE_URL,
    parse_product_page,
    parse_search_results,
)
from shoe_tracker.models import CanonicalShoe

FIXTURES = Path(__file__).parent / "fixtures" / "holabird"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text()


class _FakeClient:
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

def test_parse_search_results_returns_one_card_per_colorway():
    html = _load("search_novablast.html")
    results = parse_search_results(html)
    # Fixture: 3 mens v5 colorways + 1 womens v5 + 1 v4 clearance = 5.
    assert len(results) == 5
    assert all(r.retailer == "holabird" for r in results)
    assert all(r.product_url.startswith("https://www.holabirdsports.com/") for r in results)
    # Colorway parsed out of the parenthetical in the title.
    colorways = {r.colorway_name for r in results}
    assert "Gravel / White" in colorways
    assert "Black / Mint" in colorways


def test_parse_search_results_captures_sku_and_price_hint():
    html = _load("search_novablast.html")
    results = parse_search_results(html)
    by_url = {r.product_url.split("/")[-1]: r for r in results}
    clearance = by_url["asics-novablast-5-mens-safety-yellow-clearance"]
    assert clearance.price_hint_usd == 59.95
    assert clearance.product_code == "ASI-1011B974-750"


# --- product-page parser ---

def test_parse_product_page_returns_variants_for_both_widths():
    html = _load("product_novablast_mens_gravel.html")
    url = "https://www.holabirdsports.com/p/asics-novablast-5-mens-gravel-white"
    variants = parse_product_page(html, source_url=url)
    # Fixture: 7 D rows + 3 2E rows = 10 variants.
    assert len(variants) == 10
    assert all(isinstance(v, VariantPrice) for v in variants)
    assert all(v.colorway_name == "Gravel / White" for v in variants)
    assert all(v.price_usd == 94.95 for v in variants)
    assert {v.width for v in variants} == {"D", "2E"}


def test_parse_product_page_maps_out_of_stock_class_to_flag():
    html = _load("product_novablast_mens_gravel.html")
    variants = parse_product_page(
        html, source_url="https://www.holabirdsports.com/p/asics-novablast-5-mens-gravel-white",
    )
    by_key = {(v.size, v.width): v for v in variants}
    assert by_key[(10.5, "D")].in_stock is True
    assert by_key[(11.5, "D")].in_stock is False
    assert by_key[(11.0, "2E")].in_stock is False


def test_parse_product_page_captures_style_code_and_image():
    html = _load("product_novablast_mens_gravel.html")
    variants = parse_product_page(
        html, source_url="https://www.holabirdsports.com/p/asics-novablast-5-mens-gravel-white",
    )
    v = variants[0]
    assert v.mfr_style_code == "1011B974.020"
    assert v.image_url and "1011b974_020" in v.image_url.lower()


def test_parse_product_page_reports_current_price_not_was_price():
    html = _load("product_novablast_mens_clearance.html")
    variants = parse_product_page(
        html,
        source_url="https://www.holabirdsports.com/p/asics-novablast-5-mens-safety-yellow-clearance",
    )
    # Was $149.95, now $59.95. Must surface the current price.
    assert variants
    assert all(v.price_usd == 59.95 for v in variants)
    assert {v.in_stock for v in variants} == {True, False}


def test_parse_product_page_handles_missing_wrapper():
    assert parse_product_page("<html><body>nothing</body></html>", source_url="x") == []


# --- adapter wiring ---

def test_adapter_search_builds_search_url():
    html = _load("search_novablast.html")
    canonical = CanonicalShoe(brand="ASICS", model="Novablast", version="5", gender="mens")
    url = f"{BASE_URL}/catalogsearch/result/?q=ASICS+Novablast+5"
    client = _FakeClient({url: html})
    results = HolabirdAdapter(client=client).search(canonical)
    assert client.calls == [url]
    assert results


def test_adapter_fetches_variants_via_injected_client():
    html = _load("product_novablast_mens_gravel.html")
    url = "https://www.holabirdsports.com/p/asics-novablast-5-mens-gravel-white"
    client = _FakeClient({url: html})
    variants = HolabirdAdapter(client=client).fetch_variants(url)
    assert variants
    assert all(v.product_url == url for v in variants)


def test_adapter_advertises_metadata():
    adapter = HolabirdAdapter(client=_FakeClient({}))
    assert adapter.name == "holabird"
    assert adapter.requires_js is False
    assert adapter.supports_style_codes is True


# --- live (env-gated) ---

@pytest.mark.live
@pytest.mark.skipif(os.getenv("SHOE_TRACKER_LIVE") != "1", reason="set SHOE_TRACKER_LIVE=1 to run")
def test_live_search_and_fetch_novablast_5():
    adapter = HolabirdAdapter()
    canonical = CanonicalShoe(brand="ASICS", model="Novablast", version="5", gender="mens")
    results = adapter.search(canonical)
    assert results, "Holabird search should return at least one result"
    variants = adapter.fetch_variants(results[0].product_url)
    assert variants, f"no variants parsed from {results[0].product_url}"
