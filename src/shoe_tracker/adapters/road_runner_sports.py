"""Road Runner Sports adapter.

See docs/retailers/road_runner_sports.md for the walk-through.

Shape of the site, as of April 2026:
- Search: `GET /search?q=<query>` returns a server-rendered grid of
  `<article class="product-tile">` cards. One product (not one colorway) per
  card, unlike Running Warehouse.
- Product page: `GET /shoes/<slug>/<productId>` renders a React app. All
  variant data ships inline as JSON in `<script id="__NEXT_DATA__">`. That's
  the source of truth — we parse the JSON, not the DOM.
- VIP pricing: RRS members see a lower `VIP` price. A non-authenticated scrape
  sees the public `price`. We surface the public price; the notifier can still
  point users at the URL where they can apply their VIP discount.
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote_plus, urljoin

from selectolax.parser import HTMLParser

from ..models import CanonicalShoe
from .base import RetailerAdapter, SearchResult, VariantPrice
from .http import HttpClient, PoliteClient


BASE_URL = "https://www.roadrunnersports.com"


class RoadRunnerSportsAdapter(RetailerAdapter):
    name = "road_runner_sports"
    supports_style_codes = True
    requires_js = False
    polite_requests_per_minute = 20

    def __init__(self, client: HttpClient | None = None):
        self._client = client or PoliteClient()

    def search(self, canonical: CanonicalShoe) -> list[SearchResult]:
        url = self._search_url(canonical)
        html = self._client.get(url)
        return parse_search_results(html, base_url=BASE_URL)

    def fetch_variants(self, product_url: str) -> list[VariantPrice]:
        html = self._client.get(product_url)
        return parse_product_page(html, source_url=product_url)

    def _search_url(self, canonical: CanonicalShoe) -> str:
        terms = [canonical.brand, canonical.model]
        if canonical.version:
            terms.append(str(canonical.version))
        if canonical.variant_type and canonical.variant_type != "Wide":
            terms.append(canonical.variant_type)
        return f"{BASE_URL}/search?q={quote_plus(' '.join(terms))}"


# --- parsers (module-level so tests can call them directly) ---


def parse_search_results(html: str, *, base_url: str = BASE_URL) -> list[SearchResult]:
    tree = HTMLParser(html)
    out: list[SearchResult] = []
    for card in tree.css("article.product-tile"):
        link = card.css_first("a.product-tile-link")
        if link is None:
            continue
        href = (link.attributes.get("href") or "").strip()
        if not href:
            continue
        product_url = urljoin(base_url + "/", href)
        title_el = card.css_first(".product-tile-title")
        subtitle_el = card.css_first(".product-tile-subtitle")
        price_el = card.css_first(".price-current")

        title = title_el.text(strip=True) if title_el else ""
        subtitle = subtitle_el.text(strip=True) if subtitle_el else ""
        # Fold the gender/subtitle into the search-result title so the mapping
        # engine's token scorer can see it — "ASICS Novablast 5 Men's".
        combined = f"{title} {subtitle}".strip()

        out.append(SearchResult(
            retailer="road_runner_sports",
            title=combined,
            product_url=product_url,
            product_code=(card.attributes.get("data-product-id") or "").strip() or None,
            price_hint_usd=_parse_price(price_el.text(strip=True) if price_el else ""),
        ))
    return out


def parse_product_page(html: str, *, source_url: str) -> list[VariantPrice]:
    """Parse every variant off an RRS PDP by reading the __NEXT_DATA__ JSON."""
    payload = _extract_next_data(html)
    if payload is None:
        return []
    product = (
        payload.get("props", {})
        .get("pageProps", {})
        .get("product")
    )
    if not product:
        return []

    out: list[VariantPrice] = []
    for colorway in product.get("colorways", []) or []:
        color_name = colorway.get("colorName") or "unknown"
        color_id = colorway.get("colorId")
        image = colorway.get("image")
        mfr_style = colorway.get("mfrStyleCode")
        for v in colorway.get("variants", []) or []:
            size = v.get("size")
            price = v.get("price")
            if size is None or price is None:
                continue
            out.append(VariantPrice(
                retailer="road_runner_sports",
                product_url=source_url,
                size=float(size),
                width=str(v.get("width") or "D"),
                colorway_name=color_name,
                colorway_code=color_id,
                mfr_style_code=mfr_style,
                price_usd=float(price),
                in_stock=bool(v.get("inStock", False)),
                image_url=image,
            ))
    return out


def _extract_next_data(html: str) -> dict | None:
    """Find the Next.js data blob in the PDP and decode it.

    Parsed via selectolax rather than regex so an HTML comment that quotes
    the script tag doesn't trip us up.
    """
    tree = HTMLParser(html)
    script = tree.css_first("script#__NEXT_DATA__")
    if script is None:
        return None
    try:
        return json.loads(script.text(strip=False))
    except json.JSONDecodeError:
        return None


_PRICE_RE = re.compile(r"\$?\s*([\d,]+\.\d{2}|\d+)")


def _parse_price(s: str) -> float | None:
    if not s:
        return None
    m = _PRICE_RE.search(s)
    if not m:
        return None
    return float(m.group(1).replace(",", ""))
