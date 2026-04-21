import pytest

from shoe_tracker.adapters.base import RetailerAdapter, SearchResult, VariantPrice
from shoe_tracker.models import CanonicalShoe


def test_retailer_adapter_is_abstract():
    with pytest.raises(TypeError):
        RetailerAdapter()  # type: ignore[abstract]


def test_search_result_and_variant_price_are_frozen_dataclasses():
    sr = SearchResult(
        retailer="x", title="t", product_url="https://x/p",
        colorway_name="Black", product_code="X1",
    )
    vp = VariantPrice(
        retailer="x", product_url="https://x/p", size=10.0, width="D",
        colorway_name="Black", price_usd=99.0, in_stock=True,
    )
    with pytest.raises(Exception):
        sr.title = "other"  # frozen
    with pytest.raises(Exception):
        vp.size = 11.0  # frozen


def test_concrete_subclass_must_implement_interface():
    class Incomplete(RetailerAdapter):
        name = "x"

    with pytest.raises(TypeError):
        Incomplete()  # type: ignore[abstract]

    class Complete(RetailerAdapter):
        name = "x"

        def search(self, canonical: CanonicalShoe):
            return []

        def fetch_variants(self, product_url: str):
            return []

    adapter = Complete()
    assert adapter.search(CanonicalShoe(brand="A", model="B", gender="mens")) == []
    assert adapter.fetch_variants("https://x") == []
