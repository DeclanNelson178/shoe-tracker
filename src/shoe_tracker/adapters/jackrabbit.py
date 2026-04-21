"""JackRabbit adapter.

See docs/retailers/jackrabbit.md for the walk-through.

Shape of the site, as of April 2026:
- Shopify storefront at `jackrabbit.com`.
- Search: `GET /search?q=<query>` returns a grid of `<a class="grid-product__card">`
  anchors. One card per product (mens, womens, GTX are separate products).
- Product page: a Shopify PDP. All variants live in an inline
  `<script type="application/json" id="ProductJson-...">` block. The variant
  JSON is the canonical source — we parse it and skip the rendered picker.
- Shopify variants have three options per product: `option1`/`option2`/`option3`.
  JackRabbit consistently orders them as Size / Color / Width. Prices are in
  cents.
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote_plus, urljoin

from selectolax.parser import HTMLParser

from ..models import CanonicalShoe
from .base import RetailerAdapter, SearchResult, VariantPrice
from .http import HttpClient, PoliteClient


BASE_URL = "https://www.jackrabbit.com"


class JackrabbitAdapter(RetailerAdapter):
    name = "jackrabbit"
    supports_style_codes = False  # JackRabbit SKUs are internal, not mfr style codes
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


# --- parsers ---


_PRICE_RE = re.compile(r"\$?\s*([\d,]+\.\d{2}|\d+)")


def parse_search_results(html: str, *, base_url: str = BASE_URL) -> list[SearchResult]:
    tree = HTMLParser(html)
    out: list[SearchResult] = []
    for card in tree.css("a.grid-product__card"):
        href = (card.attributes.get("href") or "").strip()
        if not href:
            continue
        title_el = card.css_first(".grid-product__title")
        price_el = card.css_first(".grid-product__price")
        out.append(SearchResult(
            retailer="jackrabbit",
            title=title_el.text(strip=True) if title_el else "",
            product_url=urljoin(base_url + "/", href),
            product_code=(card.attributes.get("data-product-id") or "").strip() or None,
            price_hint_usd=_parse_price(price_el.text(strip=True) if price_el else ""),
        ))
    return out


def parse_product_page(html: str, *, source_url: str) -> list[VariantPrice]:
    payload = _extract_product_json(html)
    if payload is None:
        return []

    default_image = payload.get("featured_image")
    options = [o.lower() for o in (payload.get("options") or [])]
    size_idx = _option_index(options, "size")
    color_idx = _option_index(options, "color")
    width_idx = _option_index(options, "width")

    out: list[VariantPrice] = []
    for v in payload.get("variants", []) or []:
        size = _variant_option(v, size_idx)
        color = _variant_option(v, color_idx) or "unknown"
        width = _variant_option(v, width_idx) or "D"
        if size is None:
            continue
        try:
            size_val = float(size)
        except ValueError:
            continue
        price_cents = v.get("price")
        if price_cents is None:
            continue

        featured = v.get("featured_image")
        image_url = default_image
        if isinstance(featured, dict):
            image_url = featured.get("src") or image_url
        elif isinstance(featured, str):
            image_url = featured

        out.append(VariantPrice(
            retailer="jackrabbit",
            product_url=source_url,
            size=size_val,
            width=str(width),
            colorway_name=str(color),
            colorway_code=v.get("sku") or None,
            mfr_style_code=None,
            price_usd=float(price_cents) / 100.0,
            in_stock=bool(v.get("available", False)),
            image_url=image_url,
        ))
    return out


def _extract_product_json(html: str) -> dict | None:
    """Find the Shopify ProductJson <script> and decode it.

    Parsed via selectolax rather than regex so an HTML comment that happens to
    quote the script tag doesn't trip us up.
    """
    tree = HTMLParser(html)
    for script in tree.css("script[type='application/json']"):
        sid = (script.attributes.get("id") or "").strip()
        if not sid.startswith("ProductJson-"):
            continue
        raw = script.text(strip=False)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return None


def _option_index(options: list[str], wanted: str) -> int | None:
    for i, o in enumerate(options):
        if o == wanted:
            return i
    return None


def _variant_option(variant: dict, idx: int | None) -> str | None:
    if idx is None:
        return None
    key = f"option{idx + 1}"
    val = variant.get(key)
    if val is None:
        return None
    return str(val).strip()


def _parse_price(s: str) -> float | None:
    if not s:
        return None
    m = _PRICE_RE.search(s)
    if not m:
        return None
    return float(m.group(1).replace(",", ""))
