"""Holabird Sports adapter.

See docs/retailers/holabird.md for a walk-through of the page layout this
parser depends on.

Shape of the site, as of April 2026:
- Shopify storefront. Search lives at `/search?q=<query>`. Each card is a
  `.product-item--vertical` whose title link points at `/products/<slug>`.
- Product page: a Shopify PDP. Every variant lives in an inline
  `<script type="application/json" id="ProductJson-…">` block. Options are
  `["Size", "Width"]` — colorway is part of the product title, not a variant
  option (one PDP per colorway, like Running Warehouse).
- Manufacturer style code is rendered as a spec block with id `style-number`
  and a `title="Style #: 1011B974.020"` attribute.
- Sale variants carry both `price` and `compare_at_price` (cents). We always
  surface `price` (the current sale price).
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote_plus, urljoin, urlsplit, urlunsplit

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
        return f"{BASE_URL}/search?q={quote_plus(' '.join(terms))}"


# --- parsers ---


_PRICE_RE = re.compile(r"\$?\s*([\d,]+\.\d{2}|\d+)")
_ITEM_NUMBER_RE = re.compile(r"Item\s*#\s*([A-Z0-9-]+)", re.IGNORECASE)
_STYLE_NUMBER_RE = re.compile(r"Style\s*#?\s*:?\s*([A-Z0-9.\-]+)", re.IGNORECASE)
_COLORWAY_FROM_TITLE_RE = re.compile(
    r"\b(?:Men'?s|Women'?s|Unisex|Kids'?)\s+(.+?)$", re.IGNORECASE
)

# Map Holabird's verbose width labels to the short form used elsewhere.
# Falls through to the verbatim first token for anything we haven't seen.
_WIDTH_NORMALIZE = {
    "D": "D",
    "EE": "2E",
    "B": "B",
    "2A": "2A",
    "4E": "4E",
}


def parse_search_results(html: str, *, base_url: str = BASE_URL) -> list[SearchResult]:
    tree = HTMLParser(html)
    out: list[SearchResult] = []
    for card in tree.css(".product-item--vertical"):
        link = card.css_first("a.product-item__title")
        if link is None:
            continue
        href = (link.attributes.get("href") or "").strip()
        if not href.startswith("/products/"):
            continue
        product_url = urljoin(base_url + "/", _strip_query(href))
        title_attr = (link.attributes.get("title") or link.text(strip=True) or "").strip()
        # The `title` attribute looks like ": ASICS Novablast 5 Men's Gravel/White (Item #043982)".
        # The leading ": " is decorative; strip it off.
        title = title_attr.lstrip(": ").strip()
        colorway = _colorway_from_title(title)
        item_number = _item_number(title_attr)
        price = _current_card_price(card)
        out.append(SearchResult(
            retailer="holabird",
            title=title,
            product_url=product_url,
            colorway_name=colorway,
            product_code=item_number,
            price_hint_usd=price,
        ))
    return out


def parse_product_page(html: str, *, source_url: str) -> list[VariantPrice]:
    payload = _extract_product_json(html)
    if payload is None:
        return []

    options = [str(o).strip().lower() for o in (payload.get("options") or [])]
    size_idx = _option_index(options, "size")
    width_idx = _option_index(options, "width")
    image_url = _normalize_image(payload.get("featured_image"))
    colorway_name = _colorway_from_title(payload.get("title") or "") or "unknown"
    style_code = _find_style_number(html)

    out: list[VariantPrice] = []
    for v in payload.get("variants", []) or []:
        size_str = _variant_option(v, size_idx)
        if size_str is None:
            continue
        try:
            size_val = float(size_str)
        except ValueError:
            continue
        width_label = _variant_option(v, width_idx) or "D"
        price_cents = v.get("price")
        if price_cents is None:
            continue
        out.append(VariantPrice(
            retailer="holabird",
            product_url=source_url,
            size=size_val,
            width=_normalize_width(width_label),
            colorway_name=colorway_name,
            colorway_code=None,
            mfr_style_code=style_code,
            price_usd=float(price_cents) / 100.0,
            in_stock=bool(v.get("available", False)),
            image_url=image_url,
        ))
    return out


# --- helpers ---


def _extract_product_json(html: str) -> dict | None:
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
    val = variant.get(f"option{idx + 1}")
    return None if val is None else str(val).strip()


def _normalize_width(label: str) -> str:
    # Holabird option labels look like "D - Medium" or "EE - Wide".
    head = label.split(" - ", 1)[0].strip()
    return _WIDTH_NORMALIZE.get(head.upper(), head or "D")


def _normalize_image(src: str | dict | None) -> str | None:
    if src is None:
        return None
    if isinstance(src, dict):
        src = src.get("src")
        if src is None:
            return None
    src = str(src).strip()
    if src.startswith("//"):
        return "https:" + src
    return src


def _strip_query(href: str) -> str:
    parts = urlsplit(href)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _colorway_from_title(title: str) -> str:
    # Strip a trailing parenthetical (e.g., "(Item #043982)") if present, then
    # take everything after "Men's"/"Women's"/"Unisex".
    cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()
    m = _COLORWAY_FROM_TITLE_RE.search(cleaned)
    return m.group(1).strip() if m else cleaned


def _item_number(title_attr: str) -> str | None:
    m = _ITEM_NUMBER_RE.search(title_attr)
    return m.group(1) if m else None


def _current_card_price(card) -> float | None:
    # Sale cards carry both `.price--highlight` (current) and `.price--compare`
    # (regular). Plain cards have just `.price`. The current price is whichever
    # `.price` node lacks the `--compare` modifier.
    for node in card.css(".product-item__price-list .price"):
        classes = (node.attributes.get("class") or "").split()
        if "price--compare" in classes:
            continue
        price = _parse_price(node.text(strip=True))
        if price is not None:
            return price
    return None


def _find_style_number(html: str) -> str | None:
    tree = HTMLParser(html)
    node = tree.css_first("#style-number")
    if node is None:
        return None
    title_attr = (node.attributes.get("title") or "").strip()
    m = _STYLE_NUMBER_RE.search(title_attr) if title_attr else None
    if not m:
        m = _STYLE_NUMBER_RE.search(node.text(strip=True))
    return m.group(1) if m else None


def _parse_price(s: str) -> float | None:
    if not s:
        return None
    m = _PRICE_RE.search(s)
    if not m:
        return None
    return float(m.group(1).replace(",", ""))
