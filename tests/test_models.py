import pytest
from pydantic import ValidationError

from shoe_tracker.models import (
    CanonicalShoe,
    RotationConfig,
    RotationShoe,
    WatchlistEntry,
)


def test_canonical_shoe_display_name_includes_variant_type():
    shoe = CanonicalShoe(
        brand="ASICS", model="Novablast", version="5", gender="mens", variant_type=None,
    )
    assert shoe.display_name == "ASICS Novablast 5"

    gtx = CanonicalShoe(
        brand="Nike", model="Vomero", version="18", gender="mens", variant_type="GTX",
    )
    assert gtx.display_name == "Nike Vomero 18 GTX"


def test_canonical_shoe_rejects_empty_brand():
    with pytest.raises(ValidationError):
        CanonicalShoe(brand="  ", model="X", gender="mens")


def test_watchlist_threshold_must_be_positive():
    with pytest.raises(ValidationError):
        WatchlistEntry(canonical_shoe_id=1, size=10.0, threshold_usd=0)


def test_watchlist_colorway_list_strips_and_drops_blanks():
    e = WatchlistEntry(
        canonical_shoe_id=1, size=10.0, threshold_usd=100,
        colorway_policy="denylist", colorway_list=[" Ugly ", "", "Muddy"],
    )
    assert e.colorway_list == ["Ugly", "Muddy"]


def test_rotation_config_accepts_minimal_entry():
    cfg = RotationConfig(
        user_email="me@example.com",
        shoes=[
            RotationShoe(
                brand="ASICS", model="Novablast", version="5", gender="mens",
                size=10.5, width="D", threshold_usd=100,
            )
        ],
    )
    assert cfg.shoes[0].colorway_policy == "any"
    assert cfg.shoes[0].colorway_list == []


def test_rotation_config_rejects_bad_gender():
    with pytest.raises(ValidationError):
        RotationConfig(
            user_email="me@example.com",
            shoes=[
                RotationShoe(
                    brand="ASICS", model="Novablast", gender="men",  # wrong
                    size=10.5, threshold_usd=100,
                )
            ],
        )
