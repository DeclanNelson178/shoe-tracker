"""JackRabbit adapter tests.

Unit tests parse fixtures under `tests/fixtures/jackrabbit/`. The
`@pytest.mark.live` test at the bottom hits the real site — skipped unless
`SHOE_TRACKER_LIVE=1` is set.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from shoe_tracker.adapters import JackrabbitAdapter
from shoe_tracker.adapters.base import VariantPrice
from shoe_tracker.adapters.jackrabbit import (
    BASE_URL,
    parse_product_page,
    parse_search_results,
)
from shoe_tracker.models import CanonicalShoe


FIXTURES = Path(__file__).parent / "fixtures" / "jackrabbit"


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

def test_parse_search_results_returns_one_card_per_product():
    html = _load("search_novablast.html")
    results = parse_search_results(html)
    # Fixture: mens + womens + GTX = 3 cards.
    assert len(results) == 3
    assert all(r.retailer == "jackrabbit" for r in results)
    assert all(r.product_url.startswith("https://www.jackrabbit.com/") for r in results)


def test_parse_search_results_extracts_product_id_and_price_hint():
    html = _load("search_novablast.html")
    results = parse_search_results(html)
    ids = {r.product_code for r in results}
    assert "7123456789" in ids
    assert all(r.price_hint_usd and r.price_hint_usd >= 149.95 for r in results)


# --- product-page parser ---

def test_parse_product_page_reads_every_shopify_variant():
    html = _load("product_novablast_mens.html")
    url = "https://www.jackrabbit.com/products/asics-novablast-5-mens"
    variants = parse_product_page(html, source_url=url)
    # 9 Gravel/White variants + 4 Black/Mint variants = 13 total.
    assert len(variants) == 13
    assert all(isinstance(v, VariantPrice) for v in variants)
    assert all(v.retailer == "jackrabbit" for v in variants)
    assert all(v.product_url == url for v in variants)


def test_parse_product_page_decodes_cents_price():
    html = _load("product_novablast_mens.html")
    variants = parse_product_page(
        html, source_url="https://www.jackrabbit.com/products/asics-novablast-5-mens",
    )
    gw = [v for v in variants if v.colorway_name == "Gravel/White"]
    bm = [v for v in variants if v.colorway_name == "Black/Mint"]
    # 14995 cents = $149.95; 11995 cents = $119.95.
    assert all(v.price_usd == 149.95 for v in gw)
    assert all(v.price_usd == 119.95 for v in bm)


def test_parse_product_page_honors_available_flag_and_width():
    html = _load("product_novablast_mens.html")
    variants = parse_product_page(
        html, source_url="https://www.jackrabbit.com/products/asics-novablast-5-mens",
    )
    by_key = {(v.colorway_name, v.size, v.width): v for v in variants}
    assert by_key[("Gravel/White", 10.5, "D")].in_stock is True
    assert by_key[("Gravel/White", 11.5, "D")].in_stock is False
    assert by_key[("Gravel/White", 10.5, "2E")].in_stock is False
    assert by_key[("Black/Mint", 11.0, "D")].in_stock is False


def test_parse_product_page_prefers_variant_featured_image():
    html = _load("product_novablast_mens.html")
    variants = parse_product_page(
        html, source_url="https://www.jackrabbit.com/products/asics-novablast-5-mens",
    )
    gw = next(v for v in variants if v.colorway_name == "Gravel/White")
    bm = next(v for v in variants if v.colorway_name == "Black/Mint")
    assert gw.image_url and "gravel" in gw.image_url.lower()
    assert bm.image_url and "blackmint" in bm.image_url.lower()


def test_parse_product_page_returns_empty_when_script_missing():
    assert parse_product_page("<html><body>no script</body></html>", source_url="x") == []


# --- adapter wiring ---

def test_adapter_search_builds_search_url():
    html = _load("search_novablast.html")
    canonical = CanonicalShoe(brand="ASICS", model="Novablast", version="5", gender="mens")
    url = f"{BASE_URL}/search?q=ASICS+Novablast+5"
    client = _FakeClient({url: html})
    results = JackrabbitAdapter(client=client).search(canonical)
    assert client.calls == [url]
    assert results


def test_adapter_fetches_variants_via_injected_client():
    html = _load("product_novablast_mens.html")
    url = "https://www.jackrabbit.com/products/asics-novablast-5-mens"
    client = _FakeClient({url: html})
    variants = JackrabbitAdapter(client=client).fetch_variants(url)
    assert variants
    assert all(v.product_url == url for v in variants)


def test_adapter_advertises_metadata():
    adapter = JackrabbitAdapter(client=_FakeClient({}))
    assert adapter.name == "jackrabbit"
    assert adapter.requires_js is False
    # JackRabbit uses internal SKUs, not manufacturer style codes.
    assert adapter.supports_style_codes is False


# --- live (env-gated) ---

@pytest.mark.live
@pytest.mark.skipif(os.getenv("SHOE_TRACKER_LIVE") != "1", reason="set SHOE_TRACKER_LIVE=1 to run")
def test_live_search_and_fetch_novablast_5():
    adapter = JackrabbitAdapter()
    canonical = CanonicalShoe(brand="ASICS", model="Novablast", version="5", gender="mens")
    results = adapter.search(canonical)
    assert results, "JackRabbit search should return at least one result"
    variants = adapter.fetch_variants(results[0].product_url)
    assert variants, f"no variants parsed from {results[0].product_url}"
