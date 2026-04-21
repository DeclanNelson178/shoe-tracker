"""Road Runner Sports adapter tests.

Unit tests parse fixtures under `tests/fixtures/road_runner_sports/`. The
`@pytest.mark.live` test at the bottom hits the real site — skipped unless
`SHOE_TRACKER_LIVE=1` is set.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from shoe_tracker.adapters import RoadRunnerSportsAdapter
from shoe_tracker.adapters.base import VariantPrice
from shoe_tracker.adapters.road_runner_sports import (
    BASE_URL,
    parse_product_page,
    parse_search_results,
)
from shoe_tracker.models import CanonicalShoe


FIXTURES = Path(__file__).parent / "fixtures" / "road_runner_sports"


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
    # Fixture models mens + womens + GTX — one card each.
    assert len(results) == 3
    assert all(r.retailer == "road_runner_sports" for r in results)
    assert all(r.product_url.startswith("https://www.roadrunnersports.com/") for r in results)
    # Title folds subtitle in so the mapping engine can see the gender.
    assert any("Men's" in r.title for r in results)
    assert any("Women's" in r.title for r in results)
    assert any("GTX" in r.title for r in results)


def test_parse_search_results_extracts_product_id_and_price_hint():
    html = _load("search_novablast.html")
    results = parse_search_results(html)
    by_id = {r.product_code: r for r in results}
    assert set(by_id) == {"40000123", "40000124", "40000130"}
    assert by_id["40000123"].price_hint_usd == 149.99
    assert by_id["40000130"].price_hint_usd == 169.99


# --- product-page parser ---

def test_parse_product_page_reads_every_variant_from_next_data():
    html = _load("product_novablast_mens.html")
    url = "https://www.roadrunnersports.com/shoes/asics-novablast-5-mens/40000123"
    variants = parse_product_page(html, source_url=url)
    # Fixture: 10 variants in Gravel/White + 4 in Blue/Tangerine + 3 in Black/Mint = 17.
    assert len(variants) == 17
    assert all(isinstance(v, VariantPrice) for v in variants)
    assert all(v.retailer == "road_runner_sports" for v in variants)
    assert all(v.product_url == url for v in variants)
    colorways = {v.colorway_name for v in variants}
    assert colorways == {"Gravel / White", "Thunder Blue / Tangerine", "Black / Mint Tint"}


def test_parse_product_page_preserves_stock_and_width():
    html = _load("product_novablast_mens.html")
    variants = parse_product_page(
        html, source_url="https://www.roadrunnersports.com/shoes/asics-novablast-5-mens/40000123",
    )
    # 10.5 D in Gravel/White: in stock, 149.99; 10.5 2E: out of stock; 11.5 D: out of stock.
    gw = [v for v in variants if v.colorway_name == "Gravel / White"]
    widths = {v.width for v in gw}
    assert widths == {"D", "2E"}
    gw_by_key = {(v.size, v.width): v for v in gw}
    assert gw_by_key[(10.5, "D")].in_stock is True
    assert gw_by_key[(10.5, "D")].price_usd == 149.99
    assert gw_by_key[(10.5, "2E")].in_stock is False
    assert gw_by_key[(11.5, "D")].in_stock is False


def test_parse_product_page_captures_mfr_style_and_image():
    html = _load("product_novablast_mens.html")
    variants = parse_product_page(
        html, source_url="https://www.roadrunnersports.com/shoes/asics-novablast-5-mens/40000123",
    )
    black_mint = next(v for v in variants if v.colorway_name == "Black / Mint Tint")
    assert black_mint.mfr_style_code == "1011B974.001"
    assert black_mint.colorway_code == "BLACK_MINT"
    assert black_mint.image_url and "black_mint" in black_mint.image_url


def test_parse_product_page_sale_colorway_has_lower_price():
    html = _load("product_novablast_mens.html")
    variants = parse_product_page(
        html, source_url="https://www.roadrunnersports.com/shoes/asics-novablast-5-mens/40000123",
    )
    bt = [v for v in variants if v.colorway_name == "Thunder Blue / Tangerine"]
    # Sale colorway in the fixture is priced at $119.99 — whole colorway.
    assert bt and all(v.price_usd == 119.99 for v in bt)


def test_parse_product_page_returns_empty_when_next_data_missing():
    assert parse_product_page("<html><body>no data</body></html>", source_url="x") == []


# --- adapter wiring ---

def test_adapter_search_builds_search_url_and_parses():
    html = _load("search_novablast.html")
    canonical = CanonicalShoe(brand="ASICS", model="Novablast", version="5", gender="mens")
    url = f"{BASE_URL}/search?q=ASICS+Novablast+5"
    client = _FakeClient({url: html})
    adapter = RoadRunnerSportsAdapter(client=client)
    results = adapter.search(canonical)
    assert client.calls == [url]
    assert results
    assert all(r.retailer == "road_runner_sports" for r in results)


def test_adapter_search_appends_variant_type_for_gtx():
    html = _load("search_novablast.html")
    canonical = CanonicalShoe(
        brand="ASICS", model="Novablast", version="5", gender="mens", variant_type="GTX",
    )
    url = f"{BASE_URL}/search?q=ASICS+Novablast+5+GTX"
    client = _FakeClient({url: html})
    RoadRunnerSportsAdapter(client=client).search(canonical)
    assert "GTX" in client.calls[0]


def test_adapter_fetches_variants_via_injected_client():
    html = _load("product_novablast_mens.html")
    url = "https://www.roadrunnersports.com/shoes/asics-novablast-5-mens/40000123"
    client = _FakeClient({url: html})
    variants = RoadRunnerSportsAdapter(client=client).fetch_variants(url)
    assert variants and all(v.product_url == url for v in variants)


def test_adapter_advertises_metadata():
    adapter = RoadRunnerSportsAdapter(client=_FakeClient({}))
    assert adapter.name == "road_runner_sports"
    assert adapter.requires_js is False
    assert adapter.supports_style_codes is True


# --- live (env-gated) ---

@pytest.mark.live
@pytest.mark.skipif(os.getenv("SHOE_TRACKER_LIVE") != "1", reason="set SHOE_TRACKER_LIVE=1 to run")
def test_live_search_and_fetch_novablast_5():
    adapter = RoadRunnerSportsAdapter()
    canonical = CanonicalShoe(brand="ASICS", model="Novablast", version="5", gender="mens")
    results = adapter.search(canonical)
    assert results, "RRS search should return at least one result"
    variants = adapter.fetch_variants(results[0].product_url)
    assert variants, f"no variants parsed from {results[0].product_url}"
    assert any(v.in_stock for v in variants), "expected at least one in-stock variant"
