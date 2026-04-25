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
    # Captured 2026-04: Holabird's first search page returns 24 product cards.
    assert len(results) == 24
    assert all(r.retailer == "holabird" for r in results)
    assert all(r.product_url.startswith("https://www.holabirdsports.com/products/") for r in results)
    # Query-string tracking params get stripped so the URL is stable.
    assert all("?" not in r.product_url for r in results)
    colorways = {r.colorway_name for r in results}
    assert "Gravel/White" in colorways


def test_parse_search_results_captures_item_number_and_price_hint():
    html = _load("search_novablast.html")
    results = parse_search_results(html)
    by_url = {r.product_url.rsplit("/", 1)[-1]: r for r in results}
    gravel = by_url["asics-novablast-5-mens-gravel-white"]
    assert gravel.price_hint_usd == 149.95
    assert gravel.product_code == "043982"


def test_parse_search_results_skips_non_product_links():
    # Hand-crafted page mixing a blog-post card (title links to /blogs/...)
    # with one product card. Only the product card should make it through.
    html = """
    <html><body>
      <div class="product-item product-item--vertical">
        <a class="product-item__title" href="/blogs/news/article" title="Article">Some article</a>
        <div class="product-item__price-list"><span class="price">Sale price$0</span></div>
      </div>
      <div class="product-item product-item--vertical">
        <a class="product-item__title" href="/products/foo-bar?x=1"
           title=": Foo Bar Men's Black/White (Item #999999)">Foo</a>
        <div class="product-item__price-list"><span class="price">Sale price$120.00</span></div>
      </div>
    </body></html>
    """
    results = parse_search_results(html)
    assert len(results) == 1
    assert results[0].product_url == "https://www.holabirdsports.com/products/foo-bar"


def test_parse_search_results_uses_sale_price_when_card_is_on_sale():
    html = _load("product_glycerin_22_mens_sale.html")  # not a search page; use the sale fixture below
    # The sale fixture includes a "related products" rail; we just sanity-check
    # the regex against a hand-rolled card here.
    sale_card_html = """
    <div class="product-item product-item--vertical">
      <a class="product-item__title" href="/products/x"
         title=": ASICS Sale Men's Red (Item #000001)">x</a>
      <div class="product-item__price-list price-list">
        <span class="price price--highlight"><span class="visually-hidden">Sale price</span>$99.95</span>
        <span class="price price--compare"><span class="visually-hidden">Regular price</span>$165.00</span>
      </div>
    </div>
    """
    results = parse_search_results(sale_card_html)
    assert len(results) == 1
    # Current (sale) price wins over the compare-at price.
    assert results[0].price_hint_usd == 99.95
    assert html  # silence unused-variable warning


# --- product-page parser ---

def test_parse_product_page_returns_all_size_width_variants():
    html = _load("product_novablast_mens_gravel.html")
    url = "https://www.holabirdsports.com/products/asics-novablast-5-mens-gravel-white"
    variants = parse_product_page(html, source_url=url)
    # Fixture: 10 sizes × 2 widths = 20 variants.
    assert len(variants) == 20
    assert all(isinstance(v, VariantPrice) for v in variants)
    assert all(v.colorway_name == "Gravel/White" for v in variants)
    assert all(v.price_usd == 149.95 for v in variants)
    assert {v.width for v in variants} == {"D", "2E"}


def test_parse_product_page_normalizes_width_labels():
    html = _load("product_novablast_mens_gravel.html")
    variants = parse_product_page(
        html,
        source_url="https://www.holabirdsports.com/products/asics-novablast-5-mens-gravel-white",
    )
    # Holabird's option2 is "D - Medium" / "EE - Wide"; we surface "D" / "2E"
    # to match the convention used elsewhere in the system.
    by_width = {v.width for v in variants}
    assert "EE" not in by_width
    assert "2E" in by_width


def test_parse_product_page_maps_available_to_in_stock_flag():
    html = _load("product_novablast_mens_gravel.html")
    variants = parse_product_page(
        html,
        source_url="https://www.holabirdsports.com/products/asics-novablast-5-mens-gravel-white",
    )
    by_key = {(v.size, v.width): v for v in variants}
    # Captured state: one in-stock SKU (9.5 / 2E), everything else OOS.
    assert by_key[(9.5, "2E")].in_stock is True
    assert by_key[(10.5, "D")].in_stock is False


def test_parse_product_page_captures_style_code_and_image():
    html = _load("product_novablast_mens_gravel.html")
    variants = parse_product_page(
        html,
        source_url="https://www.holabirdsports.com/products/asics-novablast-5-mens-gravel-white",
    )
    v = variants[0]
    assert v.mfr_style_code == "1011B974.020"
    # Holabird image URLs come back protocol-relative; we normalize to https://.
    assert v.image_url and v.image_url.startswith("https://")
    assert "043982" in v.image_url


def test_parse_product_page_reports_current_price_not_compare_at():
    html = _load("product_glycerin_22_mens_sale.html")
    variants = parse_product_page(
        html,
        source_url="https://www.holabirdsports.com/products/brooks-glycerin-22-mens-black-grey-white",
    )
    # Was $165.00, now $114.95. Must surface the current price.
    assert variants
    assert all(v.price_usd == 114.95 for v in variants)
    # And the colorway parsed out of the product title.
    assert all(v.colorway_name == "Black/Grey/White" for v in variants)


def test_parse_product_page_handles_missing_product_json():
    assert parse_product_page("<html><body>nothing</body></html>", source_url="x") == []


# --- adapter wiring ---

def test_adapter_search_builds_search_url():
    html = _load("search_novablast.html")
    canonical = CanonicalShoe(brand="ASICS", model="Novablast", version="5", gender="mens")
    url = f"{BASE_URL}/search?q=ASICS+Novablast+5"
    client = _FakeClient({url: html})
    results = HolabirdAdapter(client=client).search(canonical)
    assert client.calls == [url]
    assert results


def test_adapter_fetches_variants_via_injected_client():
    html = _load("product_novablast_mens_gravel.html")
    url = "https://www.holabirdsports.com/products/asics-novablast-5-mens-gravel-white"
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
