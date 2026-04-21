"""Holabird Sports adapter.

See docs/retailers/holabird.md for a walk-through of the page layout this
parser depends on.

Shape of the site, as of April 2026:
- Search: `GET /catalogsearch/result/?q=<query>` renders a Magento 2
  `.product-items` list. One URL per colorway (like Running Warehouse).
- Product page: one PDP per colorway. Price is product-level (Holabird does
  not vary price per size/width on a single colorway). Sizes render as a
  `<ul class="product-sizes" data-width="…">` list with `.in-stock` /
  `.out-of-stock` classes.
- Style number lives in the spec table as `1011B974.020` form.
- Clearance colorways show a `.was-price` alongside the current `.price`. We
  always report the current price.
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus, urljoin

from selectolax.parser import HTMLParser

from ..models import CanonicalShoe
from .base import RetailerAdapter, SearchResult, VariantPrice
from .http import HttpClient, PoliteClient


BASE_URL = "https://www.holabirdsports.com"


class HolabirdAdapter(RetailerAdapter):
    name = "holabird"
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
        return f"{BASE_URL}/catalogsearch/result/?q={quote_plus(' '.join(terms))}"


# --- parsers ---


_STYLE_RE = re.compile(r"\b([A-Z0-9]{6,}\.[A-Z0-9]{2,})\b")
_PRICE_RE = re.compile(r"\$?\s*([\d,]+\.\d{2}|\d+)")
_COLOR_RE = re.compile(r"\(\s*([^)]+?)\s*\)")


def parse_search_results(html: str, *, base_url: str = BASE_URL) -> list[SearchResult]:
    tree = HTMLParser(html)
    out: list[SearchResult] = []
    for item in tree.css("li.product-item"):
        link = item.css_first("a.product-item-link")
        if link is None:
            continue
        href = (link.attributes.get("href") or "").strip()
        if not href:
            continue
        title = link.text(strip=True)
        price_el = item.css_first(".price")
        sku_el = item.css_first(".sku-hint")
        colorway = _colorway_from_title(title)

        out.append(SearchResult(
            retailer="holabird",
            title=title,
            product_url=urljoin(base_url + "/", href),
            colorway_name=colorway,
            product_code=(sku_el.attributes.get("data-sku") or "").strip() if sku_el else None,
            price_hint_usd=_parse_price(price_el.text(strip=True) if price_el else ""),
        ))
    return out


def parse_product_page(html: str, *, source_url: str) -> list[VariantPrice]:
    tree = HTMLParser(html)

    color_el = tree.css_first(".product-color")
    colorway_name = _colorway_from_label(color_el.text(strip=True)) if color_el else ""
    if not colorway_name:
        title_el = tree.css_first("title")
        if title_el is not None:
            colorway_name = _colorway_from_title(title_el.text())
    if not colorway_name:
        colorway_name = "unknown"

    # Pull the *current* price from the first non-was .price. The .was-price
    # element shares the .price class but gets skipped explicitly.
    price = _current_price(tree)
    if price is None:
        return []

    mfr_style = _find_style(tree)
    image_el = tree.css_first("img.product-image-photo")
    image_url = image_el.attributes.get("src") if image_el else None

    out: list[VariantPrice] = []
    for ul in tree.css("ul.product-sizes"):
        width = (ul.attributes.get("data-width") or "D").strip() or "D"
        for li in ul.css("li.size-option"):
            size_str = (li.attributes.get("data-size") or "").strip()
            if not size_str:
                continue
            try:
                size = float(size_str)
            except ValueError:
                continue
            classes = (li.attributes.get("class") or "").split()
            in_stock = "in-stock" in classes and "out-of-stock" not in classes
            out.append(VariantPrice(
                retailer="holabird",
                product_url=source_url,
                size=size,
                width=(li.attributes.get("data-width") or width).strip() or "D",
                colorway_name=colorway_name,
                mfr_style_code=mfr_style,
                price_usd=price,
                in_stock=in_stock,
                image_url=image_url,
            ))
    return out


def _current_price(tree: HTMLParser) -> float | None:
    """Return the current (non-'was') price from the PDP's price-wrapper."""
    wrapper = tree.css_first(".price-wrapper")
    if wrapper is None:
        return None
    for node in wrapper.css(".price"):
        classes = (node.attributes.get("class") or "").split()
        if "was-price" in classes:
            continue
        price = _parse_price(node.text(strip=True))
        if price is not None:
            return price
    return None


def _colorway_from_label(label: str) -> str:
    # "Color: Gravel / White" → "Gravel / White"
    if ":" not in label:
        return label.strip()
    return label.split(":", 1)[1].strip()


def _colorway_from_title(title: str) -> str:
    m = _COLOR_RE.search(title)
    return m.group(1).strip() if m else ""


def _find_style(tree: HTMLParser) -> str | None:
    table = tree.css_first("table.product-spec-table")
    if table is None:
        return None
    for row in table.css("tr"):
        th = row.css_first("th")
        if th and "style" in th.text(strip=True).lower():
            td = row.css_first("td")
            if td:
                m = _STYLE_RE.search(td.text(strip=True))
                return m.group(1) if m else td.text(strip=True) or None
    return None


def _parse_price(s: str) -> float | None:
    if not s:
        return None
    m = _PRICE_RE.search(s)
    if not m:
        return None
    return float(m.group(1).replace(",", ""))
