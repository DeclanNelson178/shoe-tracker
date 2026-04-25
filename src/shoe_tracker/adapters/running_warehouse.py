"""Running Warehouse adapter.

See docs/retailers/running_warehouse.md for a walk-through of the page layout
this parser depends on.

Shape of the site, as of April 2026:
- Search lives at `/search-mens.html?searchtext=...` and `/search-womens.html`.
  Each product *colorway* shows up as its own result card, with a link to
  `/<Brand>_<Model>/descpage-<PRODUCT_CODE>.html`.
- Each descpage is one colorway. It contains a table of offers, one `<tr
  class="js-ordering-subproduct">` per size+width. Each row embeds price, the
  "In Stock" count, a manufacturer style number (`Model Number: NNNNNNN.XXX`),
  and a `data-code` combining product code + size code + width letter.
- Sizes that are completely sold out are not rendered at all — the page only
  shows sizes with any inventory. Sizes with `In Stock: 0` would render as
  out-of-stock; we see those rarely but handle them explicitly.
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus, urljoin

from selectolax.parser import HTMLParser

from ..models import CanonicalShoe
from .base import RetailerAdapter, SearchResult, VariantPrice
from .http import HttpClient, PoliteClient

BASE_URL = "https://www.runningwarehouse.com"


class RunningWarehouseAdapter(RetailerAdapter):
    name = "running_warehouse"
    supports_style_codes = True
    requires_js = False
    polite_requests_per_minute = 30

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
        path = "search-womens.html" if canonical.gender == "womens" else "search-mens.html"
        terms = [canonical.brand, canonical.model]
        if canonical.version:
            terms.append(str(canonical.version))
        if canonical.variant_type and canonical.variant_type != "Wide":
            terms.append(canonical.variant_type)
        query = " ".join(terms)
        return f"{BASE_URL}/{path}?searchtext={quote_plus(query)}"


# --- parsers (module-level so tests can call them directly) ---


_PRICE_RE = re.compile(r"\$?\s*([\d,]+\.\d{2}|\d+)")
_SIZE_WIDTH_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s+(\S+)\s*$")
_MODEL_NUMBER_RE = re.compile(r"Model Number:\s*</b>\s*([A-Z0-9.]+)")


def parse_search_results(html: str, *, base_url: str = BASE_URL) -> list[SearchResult]:
    tree = HTMLParser(html)
    out: list[SearchResult] = []
    for cell in tree.css("div.cattable-wrap-cell.gtm_impression"):
        link = cell.css_first("a[href*='descpage-']")
        if link is None:
            continue
        href = (link.attributes.get("href") or "").strip()
        product_url = urljoin(base_url + "/", href)
        name_el = cell.css_first(".cattable-wrap-cell-info-name")
        sub_el = cell.css_first(".cattable-wrap-cell-info-sub")
        price_el = cell.css_first(".cattable-wrap-cell-info-price")
        title = name_el.text(strip=True) if name_el else ""
        colorway = _colorway_from_sub(sub_el.text(strip=True) if sub_el else "")
        price = _parse_price(price_el.text(strip=True) if price_el else "")
        product_code = _product_code_from_url(product_url)
        out.append(SearchResult(
            retailer="running_warehouse",
            title=title,
            product_url=product_url,
            colorway_name=colorway,
            product_code=product_code,
            price_hint_usd=price,
        ))
    return out


def parse_product_page(html: str, *, source_url: str) -> list[VariantPrice]:
    tree = HTMLParser(html)
    title_el = tree.css_first("h1.desc_top-head-title")
    style_el = tree.css_first(".desc_top-head-style")
    img_el = tree.css_first("img[itemprop='image']") or tree.css_first("img.image__shoe")
    colorway_name = _colorway_from_sub(style_el.text(strip=True) if style_el else "")
    mfr_style_code = _find_model_number(html)
    image_url = (img_el.attributes.get("src") or img_el.attributes.get("content")) if img_el else None

    # Fall back to the page title if the styled header is missing a colorway.
    if not colorway_name and title_el is not None:
        # Page title looks like "ASICS Novablast 5 Men's Shoes Gravel/White | ..."
        tag = tree.css_first("title")
        if tag is not None:
            colorway_name = _colorway_from_title(tag.text())

    out: list[VariantPrice] = []
    for row in tree.css("tr.js-ordering-subproduct"):
        code = (row.attributes.get("data-code") or "").strip()
        name_el = row.css_first("strong.js-ordering-name")
        price_el = row.css_first("span.js-ordering-price")
        stock_el = row.css_first("span.js-ordering-available")
        name = name_el.text(strip=True) if name_el else ""
        size, width = _parse_size_and_width(name)
        price = _parse_price(price_el.text(strip=True) if price_el else "")
        in_stock = _is_row_in_stock(row, stock_el)
        if size is None or price is None:
            continue
        out.append(VariantPrice(
            retailer="running_warehouse",
            product_url=source_url,
            size=size,
            width=width,
            colorway_name=colorway_name or "unknown",
            colorway_code=code[:-4] if len(code) >= 4 else None,
            mfr_style_code=mfr_style_code,
            price_usd=price,
            in_stock=in_stock,
            image_url=image_url,
        ))
    return out


def _product_code_from_url(url: str) -> str | None:
    m = re.search(r"descpage-([A-Z0-9]+)\.html", url)
    return m.group(1) if m else None


def _colorway_from_sub(sub: str) -> str:
    # "Men's Shoes - Gravel/White" → "Gravel/White"
    if not sub:
        return ""
    parts = sub.split(" - ", 1)
    return parts[-1].strip() if len(parts) == 2 else sub.strip()


def _colorway_from_title(title: str) -> str:
    # "ASICS Novablast 5 Men's Shoes Gravel/White | Running Warehouse"
    head = title.split("|")[0].strip()
    m = re.search(r"(?:Men'?s|Women'?s|Unisex)\s+Shoes?\s+(.+)", head)
    return m.group(1).strip() if m else ""


def _parse_price(s: str) -> float | None:
    if not s:
        return None
    m = _PRICE_RE.search(s)
    if not m:
        return None
    return float(m.group(1).replace(",", ""))


def _parse_size_and_width(name: str) -> tuple[float | None, str]:
    m = _SIZE_WIDTH_RE.search(name)
    if not m:
        return None, "D"
    return float(m.group(1)), m.group(2)


def _is_row_in_stock(row, stock_el) -> bool:
    if stock_el is not None:
        count = stock_el.text(strip=True)
        if count and count != "0":
            return True
        if count == "0":
            return False
    classes = (row.attributes.get("class") or "").split()
    if "js-ordering-out-of-stock" in classes or "out-of-stock" in classes:
        return False
    # Visible "Notify Me" button for a missing variant means it's OOS.
    if row.css_first("a.js-notify-me, .js-ordering-notify") is not None:
        return False
    # Default: if the row rendered at all without a stock count, treat as OOS.
    return stock_el is not None


def _find_model_number(html: str) -> str | None:
    m = _MODEL_NUMBER_RE.search(html)
    return m.group(1) if m else None
