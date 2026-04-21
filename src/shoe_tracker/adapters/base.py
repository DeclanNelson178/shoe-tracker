"""Retailer adapter contract.

Every retailer scraper implements `RetailerAdapter`. The evaluator and the
mapping engine only ever talk to this interface — individual retailer quirks
stay contained inside the adapter module.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..models import CanonicalShoe


@dataclass(frozen=True)
class SearchResult:
    """One candidate product listing returned from a retailer search.

    On retailers that ship one URL per colorway (Running Warehouse), a single
    canonical shoe yields multiple SearchResults — one per colorway page. The
    mapping engine is responsible for picking the right one(s).
    """
    retailer: str
    title: str
    product_url: str
    colorway_name: str | None = None
    mfr_style_code: str | None = None
    product_code: str | None = None
    price_hint_usd: float | None = None


@dataclass(frozen=True)
class VariantPrice:
    """One size+colorway+width offer at one retailer, at scrape time."""
    retailer: str
    product_url: str
    size: float
    width: str
    colorway_name: str
    price_usd: float
    in_stock: bool
    colorway_code: str | None = None
    mfr_style_code: str | None = None
    image_url: str | None = None


class RetailerAdapter(ABC):
    """Contract all retailer scrapers implement."""

    name: str
    supports_style_codes: bool = False
    requires_js: bool = False
    polite_requests_per_minute: int = 30

    @abstractmethod
    def search(self, canonical: CanonicalShoe) -> list[SearchResult]:
        """Return candidate product listings for a canonical shoe."""

    @abstractmethod
    def fetch_variants(self, product_url: str) -> list[VariantPrice]:
        """Return every variant listed on the given product page.

        Must include out-of-stock variants the page shows explicitly. Sizes the
        retailer hides entirely when sold out are, necessarily, not returned.
        """
