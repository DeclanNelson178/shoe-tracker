"""Mapping engine tests.

Covers the scoring rules in plan.md chunk 3:
- Hard rejects (brand, gender, version, variant_type mismatches)
- Style-code prefix match → auto 0.99
- Token overlap with distinctive-token weighting
- Tier thresholds (>=0.9 auto, 0.6–0.9 flagged, <0.6 rejected)

The scoring function is deliberately retailer-agnostic: it takes a
CanonicalShoe + SearchResult and returns a float in [0, 1].
"""
from __future__ import annotations

import pytest

from shoe_tracker.adapters.base import SearchResult
from shoe_tracker.mapping import (
    MappingOutcome,
    MappingTier,
    pick_best,
    score_match,
)
from shoe_tracker.models import CanonicalShoe


def _sr(title: str, *, url: str | None = None, mfr: str | None = None,
        code: str | None = None) -> SearchResult:
    return SearchResult(
        retailer="running_warehouse",
        title=title,
        product_url=url or f"https://rw/{title.replace(' ', '_')}.html",
        mfr_style_code=mfr,
        product_code=code,
    )


# --- hard rejects ---

def test_reject_brand_mismatch():
    shoe = CanonicalShoe(brand="ASICS", model="Novablast", version="5", gender="mens")
    r = _sr("Nike Novablast 5 Men's")
    assert score_match(shoe, r) == 0.0


def test_reject_version_mismatch():
    shoe = CanonicalShoe(brand="ASICS", model="Novablast", version="5", gender="mens")
    r = _sr("ASICS Novablast 4 Men's")
    assert score_match(shoe, r) == 0.0


def test_reject_gender_mismatch_via_url():
    # The RW search URL already filters by gender; defense-in-depth here uses
    # the search result's URL/title to reject obvious cross-gender matches.
    shoe = CanonicalShoe(brand="ASICS", model="Novablast", version="5", gender="mens")
    r = _sr("ASICS Novablast 5 Women's", url="https://rw/ASICS_Novablast_5/descpage-ANB5W1.html")
    assert score_match(shoe, r) == 0.0


def test_reject_gtx_when_canonical_is_non_gtx():
    shoe = CanonicalShoe(brand="Nike", model="Vomero", version="18", gender="mens")
    r = _sr("Nike Vomero 18 GTX Men's")
    assert score_match(shoe, r) == 0.0


def test_reject_non_gtx_when_canonical_is_gtx():
    shoe = CanonicalShoe(
        brand="Nike", model="Vomero", version="18", gender="mens", variant_type="GTX",
    )
    r = _sr("Nike Vomero 18 Men's")
    assert score_match(shoe, r) == 0.0


def test_reject_non_trail_when_canonical_is_trail():
    shoe = CanonicalShoe(
        brand="HOKA", model="Speedgoat", version="6", gender="mens", variant_type="Trail",
    )
    r = _sr("HOKA Speedgoat 6 GTX Men's")  # Trail-specific variant required
    assert score_match(shoe, r) == 0.0


def test_reject_wide_when_canonical_is_not_wide():
    shoe = CanonicalShoe(brand="ASICS", model="Novablast", version="5", gender="mens")
    r = _sr("ASICS Novablast 5 Wide Men's")
    assert score_match(shoe, r) == 0.0


# --- style-code prefix match ---

def test_style_code_prefix_match_auto_099():
    shoe = CanonicalShoe(
        brand="ASICS", model="Novablast", version="5", gender="mens",
        mfr_style_prefix="1011B974",
    )
    r = _sr("ASICS Novablast 5", mfr="1011B974.020")
    assert score_match(shoe, r) == pytest.approx(0.99)


def test_style_code_prefix_mismatch_does_not_auto_pass():
    shoe = CanonicalShoe(
        brand="ASICS", model="Novablast", version="5", gender="mens",
        mfr_style_prefix="1011B867",
    )
    # mfr code from a different shoe: token overlap still scores, but not 0.99.
    r = _sr("ASICS Novablast 4", mfr="1011B600.020")
    # Hard reject on version 4 vs 5 → 0 regardless of style code.
    assert score_match(shoe, r) == 0.0


# --- token overlap ---

def test_exact_title_token_match_scores_high():
    shoe = CanonicalShoe(brand="ASICS", model="Novablast", version="5", gender="mens")
    r = _sr("ASICS Novablast 5")
    s = score_match(shoe, r)
    assert s >= 0.9, s


def test_noise_words_do_not_boost_score():
    # "Men's Running Shoe" shouldn't make an otherwise weak match pass.
    shoe = CanonicalShoe(brand="ASICS", model="Cumulus", version="27", gender="mens")
    r = _sr("Nike Pegasus Men's Running Shoe")  # brand mismatch — hard reject anyway
    assert score_match(shoe, r) == 0.0


def test_missing_version_in_title_still_scores_when_brand_model_match():
    shoe = CanonicalShoe(brand="ASICS", model="Novablast", version="5", gender="mens")
    r = _sr("ASICS Novablast")  # version missing — ambiguous, not a hard reject
    s = score_match(shoe, r)
    # Below auto threshold, above manual-review floor.
    assert 0.3 <= s < 0.9


# --- nasty collisions enumerated in plan.md ---

def test_speedgoat_6_vs_speedgoat_6_gtx():
    plain = CanonicalShoe(
        brand="HOKA", model="Speedgoat", version="6", gender="mens", variant_type="Trail",
    )
    gtx = CanonicalShoe(
        brand="HOKA", model="Speedgoat", version="6", gender="mens", variant_type="GTX",
    )
    gtx_result = _sr("HOKA Speedgoat 6 GTX Men's")
    plain_result = _sr("HOKA Speedgoat 6 Men's")

    assert score_match(plain, gtx_result) == 0.0
    assert score_match(plain, plain_result) >= 0.9
    assert score_match(gtx, gtx_result) >= 0.9
    assert score_match(gtx, plain_result) == 0.0


def test_speed_4_vs_speed_5():
    shoe4 = CanonicalShoe(
        brand="Saucony", model="Endorphin Speed", version="4", gender="mens",
    )
    shoe5 = CanonicalShoe(
        brand="Saucony", model="Endorphin Speed", version="5", gender="mens",
    )
    r5 = _sr("Saucony Endorphin Speed 5")
    assert score_match(shoe4, r5) == 0.0
    assert score_match(shoe5, r5) >= 0.9


def test_vomero_18_vs_vomero_18_gtx():
    shoe = CanonicalShoe(brand="Nike", model="Vomero", version="18", gender="mens")
    r_gtx = _sr("Nike Vomero 18 GTX Men's")
    r_plain = _sr("Nike Vomero 18 Men's")
    assert score_match(shoe, r_gtx) == 0.0
    assert score_match(shoe, r_plain) >= 0.9


# --- tiering + pick_best ---

def test_pick_best_returns_highest_scoring_and_auto_tier():
    shoe = CanonicalShoe(brand="ASICS", model="Novablast", version="5", gender="mens")
    results = [
        _sr("ASICS Novablast 4"),           # rejected (version)
        _sr("ASICS Novablast 5"),           # strong
        _sr("ASICS Novablast"),             # weak, ambiguous
    ]
    outcome = pick_best(shoe, results)
    assert isinstance(outcome, MappingOutcome)
    assert outcome.best is not None
    assert outcome.best.title == "ASICS Novablast 5"
    assert outcome.confidence >= 0.9
    assert outcome.tier is MappingTier.AUTO


def test_pick_best_flags_middle_tier():
    shoe = CanonicalShoe(brand="ASICS", model="Novablast", version="5", gender="mens")
    # Only ambiguous candidates survive: mid-tier confidence expected.
    outcome = pick_best(shoe, [_sr("ASICS Novablast")])
    assert outcome.best is not None
    assert outcome.tier is MappingTier.FLAGGED
    assert 0.6 <= outcome.confidence < 0.9


def test_pick_best_rejects_when_all_fail():
    shoe = CanonicalShoe(brand="ASICS", model="Novablast", version="5", gender="mens")
    outcome = pick_best(shoe, [
        _sr("ASICS Novablast 4"),
        _sr("Nike Pegasus 41"),
    ])
    assert outcome.best is None
    assert outcome.tier is MappingTier.REJECTED
    assert outcome.confidence == 0.0


def test_pick_best_empty_candidates():
    shoe = CanonicalShoe(brand="ASICS", model="Novablast", version="5", gender="mens")
    outcome = pick_best(shoe, [])
    assert outcome.best is None
    assert outcome.tier is MappingTier.REJECTED
    assert outcome.confidence == 0.0


def test_pick_best_style_code_beats_weaker_title_match():
    shoe = CanonicalShoe(
        brand="ASICS", model="Novablast", version="5", gender="mens",
        mfr_style_prefix="1011B974",
    )
    outcome = pick_best(shoe, [
        _sr("ASICS Novablast 5", mfr="9999X000.000"),       # title-match, wrong code
        _sr("ASICS Novablast 5 Shoes", mfr="1011B974.020"), # style-code match
    ])
    assert outcome.best is not None
    assert outcome.confidence == pytest.approx(0.99)
    assert outcome.best.mfr_style_code == "1011B974.020"
