"""Evaluator tests.

The evaluator scans the watchlist, looks up the latest in-stock variant prices
for every mapped retailer, applies colorway/size/width filters, compares to the
per-entry threshold, and yields a TriggeredAlert for each watchlist entry that
has a qualifying offer. Dedup is keyed off `notifications_sent`.

Tests seed the DB directly via the repos — no adapters involved.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from shoe_tracker.db import (
    Database,
    NotificationRepo,
    PriceSnapshotRepo,
    RetailerMappingRepo,
    ShoeRepo,
    UserRepo,
    WatchlistRepo,
    init_db,
)
from shoe_tracker.evaluator import TriggeredAlert, evaluate
from shoe_tracker.models import (
    CanonicalShoe,
    NotificationRecord,
    PriceSnapshot,
    RetailerMapping,
    ShoeVariant,
    User,
    WatchlistEntry,
)

NOW = datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.db"
    init_db(path)
    d = Database(path)
    UserRepo(d).upsert(User(id="me", email="me@example.com"))
    yield d
    d.close()


def _seed_novablast(db: Database) -> CanonicalShoe:
    return ShoeRepo(db).upsert_canonical(CanonicalShoe(
        brand="ASICS", model="Novablast", version="5", gender="mens",
    ))


def _add_watch(
    db: Database, shoe: CanonicalShoe, *,
    threshold: float = 100.0, size: float = 10.5, width: str = "D",
    policy: str = "any", colorway_list: list[str] | None = None,
    active: bool = True,
) -> WatchlistEntry:
    return WatchlistRepo(db).upsert(WatchlistEntry(
        user_id="me", canonical_shoe_id=shoe.id, size=size, width=width,
        colorway_policy=policy, colorway_list=colorway_list or [],
        threshold_usd=threshold, active=active,
    ))


def _add_mapping(db: Database, shoe: CanonicalShoe, retailer: str) -> None:
    RetailerMappingRepo(db).upsert(RetailerMapping(
        canonical_shoe_id=shoe.id, retailer=retailer,
        product_url=f"https://{retailer}/x", confidence=0.97,
    ))


def _add_variant(
    db: Database, shoe: CanonicalShoe, *,
    size: float = 10.5, width: str = "D",
    colorway_name: str = "Black/Mint", colorway_code: str | None = None,
) -> ShoeVariant:
    return ShoeRepo(db).upsert_variant(ShoeVariant(
        canonical_shoe_id=shoe.id, size=size, width=width,
        colorway_name=colorway_name, colorway_code=colorway_code,
    ))


def _snap(
    db: Database, variant: ShoeVariant, *,
    retailer: str = "running_warehouse", price: float = 94.95,
    in_stock: bool = True, when: datetime = NOW, url: str = "https://x/p",
) -> None:
    PriceSnapshotRepo(db).insert(PriceSnapshot(
        shoe_variant_id=variant.id, retailer=retailer, price_usd=price,
        in_stock=in_stock, scraped_at=when, source_url=url,
    ))


# --- core triggering ---

def test_alert_fires_when_price_below_threshold(db):
    shoe = _seed_novablast(db)
    _add_watch(db, shoe, threshold=100.0)
    _add_mapping(db, shoe, "running_warehouse")
    v = _add_variant(db, shoe)
    _snap(db, v, price=94.95)

    alerts = evaluate(db, now=NOW)
    assert len(alerts) == 1
    a = alerts[0]
    assert isinstance(a, TriggeredAlert)
    assert a.shoe.display_name == "ASICS Novablast 5"
    assert a.retailer == "running_warehouse"
    assert a.price_usd == 94.95
    assert a.delta_usd == pytest.approx(5.05)


def test_no_alert_when_price_above_threshold(db):
    shoe = _seed_novablast(db)
    _add_watch(db, shoe, threshold=90.0)
    _add_mapping(db, shoe, "running_warehouse")
    v = _add_variant(db, shoe)
    _snap(db, v, price=94.95)

    assert evaluate(db, now=NOW) == []


def test_alert_at_exact_threshold_still_fires(db):
    shoe = _seed_novablast(db)
    _add_watch(db, shoe, threshold=94.95)
    _add_mapping(db, shoe, "running_warehouse")
    v = _add_variant(db, shoe)
    _snap(db, v, price=94.95)

    assert len(evaluate(db, now=NOW)) == 1


# --- multi-retailer min selection ---

def test_picks_cheapest_retailer(db):
    shoe = _seed_novablast(db)
    _add_watch(db, shoe, threshold=100.0)
    _add_mapping(db, shoe, "running_warehouse")
    _add_mapping(db, shoe, "holabird")
    v = _add_variant(db, shoe)
    _snap(db, v, retailer="running_warehouse", price=94.95)
    _snap(db, v, retailer="holabird", price=89.00)

    alerts = evaluate(db, now=NOW)
    assert len(alerts) == 1
    assert alerts[0].retailer == "holabird"
    assert alerts[0].price_usd == 89.00


def test_picks_cheapest_variant_across_colorways(db):
    shoe = _seed_novablast(db)
    _add_watch(db, shoe, threshold=100.0)
    _add_mapping(db, shoe, "running_warehouse")
    v_black = _add_variant(db, shoe, colorway_name="Black/Mint")
    v_yellow = _add_variant(db, shoe, colorway_name="Safety Yellow")
    _snap(db, v_black, price=94.95)
    _snap(db, v_yellow, price=74.95)

    alerts = evaluate(db, now=NOW)
    assert len(alerts) == 1
    assert alerts[0].variant.colorway_name == "Safety Yellow"
    assert alerts[0].price_usd == 74.95


def test_uses_latest_snapshot_per_retailer(db):
    shoe = _seed_novablast(db)
    _add_watch(db, shoe, threshold=100.0)
    _add_mapping(db, shoe, "running_warehouse")
    v = _add_variant(db, shoe)
    earlier = NOW - timedelta(hours=6)
    _snap(db, v, price=150.00, when=earlier)  # stale
    _snap(db, v, price=94.95, when=NOW)       # latest

    alerts = evaluate(db, now=NOW)
    assert len(alerts) == 1
    assert alerts[0].price_usd == 94.95


# --- filters ---

def test_out_of_stock_excluded(db):
    shoe = _seed_novablast(db)
    _add_watch(db, shoe, threshold=100.0)
    _add_mapping(db, shoe, "running_warehouse")
    v = _add_variant(db, shoe)
    _snap(db, v, price=80.00, in_stock=False)

    assert evaluate(db, now=NOW) == []


def test_size_mismatch_excluded(db):
    shoe = _seed_novablast(db)
    _add_watch(db, shoe, threshold=100.0, size=10.5)
    _add_mapping(db, shoe, "running_warehouse")
    wrong_size = _add_variant(db, shoe, size=11.0)
    _snap(db, wrong_size, price=80.00)

    assert evaluate(db, now=NOW) == []


def test_width_mismatch_excluded(db):
    shoe = _seed_novablast(db)
    _add_watch(db, shoe, threshold=100.0, width="D")
    _add_mapping(db, shoe, "running_warehouse")
    wide = _add_variant(db, shoe, width="2E")
    _snap(db, wide, price=80.00)

    assert evaluate(db, now=NOW) == []


def test_inactive_watchlist_entry_skipped(db):
    shoe = _seed_novablast(db)
    _add_watch(db, shoe, threshold=100.0, active=False)
    _add_mapping(db, shoe, "running_warehouse")
    v = _add_variant(db, shoe)
    _snap(db, v, price=50.00)

    assert evaluate(db, now=NOW) == []


def test_unmapped_shoe_yields_no_alert(db):
    shoe = _seed_novablast(db)
    _add_watch(db, shoe, threshold=100.0)
    # No mapping → nothing to evaluate.
    v = _add_variant(db, shoe)
    _snap(db, v, price=50.00)

    assert evaluate(db, now=NOW) == []


# --- colorway policy ---

def test_allowlist_matches_substring_in_name(db):
    shoe = _seed_novablast(db)
    _add_watch(db, shoe, threshold=100.0, policy="allowlist", colorway_list=["Black"])
    _add_mapping(db, shoe, "running_warehouse")
    v_black = _add_variant(db, shoe, colorway_name="Black/Mint")
    v_yellow = _add_variant(db, shoe, colorway_name="Safety Yellow")
    _snap(db, v_yellow, price=50.00)
    _snap(db, v_black, price=94.95)

    alerts = evaluate(db, now=NOW)
    assert len(alerts) == 1
    assert alerts[0].variant.colorway_name == "Black/Mint"


def test_allowlist_is_case_insensitive(db):
    shoe = _seed_novablast(db)
    _add_watch(db, shoe, threshold=100.0, policy="allowlist", colorway_list=["mint"])
    _add_mapping(db, shoe, "running_warehouse")
    v = _add_variant(db, shoe, colorway_name="Black/Mint")
    _snap(db, v, price=94.95)

    assert len(evaluate(db, now=NOW)) == 1


def test_allowlist_matches_colorway_code(db):
    shoe = _seed_novablast(db)
    _add_watch(
        db, shoe, threshold=100.0,
        policy="allowlist", colorway_list=["001"],
    )
    _add_mapping(db, shoe, "running_warehouse")
    v = _add_variant(db, shoe, colorway_name="Black/Mint", colorway_code="001")
    _snap(db, v, price=94.95)

    assert len(evaluate(db, now=NOW)) == 1


def test_denylist_excludes_listed_colorways(db):
    shoe = _seed_novablast(db)
    _add_watch(
        db, shoe, threshold=100.0,
        policy="denylist", colorway_list=["Yellow"],
    )
    _add_mapping(db, shoe, "running_warehouse")
    v_black = _add_variant(db, shoe, colorway_name="Black/Mint")
    v_yellow = _add_variant(db, shoe, colorway_name="Safety Yellow")
    _snap(db, v_black, price=94.95)
    _snap(db, v_yellow, price=50.00)  # cheaper but excluded

    alerts = evaluate(db, now=NOW)
    assert len(alerts) == 1
    assert alerts[0].variant.colorway_name == "Black/Mint"


# --- dedup ---

def test_dedup_within_7_days(db):
    shoe = _seed_novablast(db)
    _add_watch(db, shoe, threshold=100.0)
    _add_mapping(db, shoe, "running_warehouse")
    v = _add_variant(db, shoe)
    _snap(db, v, price=94.95)

    NotificationRepo(db).insert(NotificationRecord(
        user_id="me", shoe_variant_id=v.id, retailer="running_warehouse",
        triggering_price=94.95, sent_at=NOW - timedelta(days=3), channel="email",
    ))

    assert evaluate(db, now=NOW) == []


def test_dedup_expires_after_7_days(db):
    shoe = _seed_novablast(db)
    _add_watch(db, shoe, threshold=100.0)
    _add_mapping(db, shoe, "running_warehouse")
    v = _add_variant(db, shoe)
    _snap(db, v, price=94.95)

    NotificationRepo(db).insert(NotificationRecord(
        user_id="me", shoe_variant_id=v.id, retailer="running_warehouse",
        triggering_price=94.95, sent_at=NOW - timedelta(days=8), channel="email",
    ))

    assert len(evaluate(db, now=NOW)) == 1


def test_dedup_per_variant_retailer(db):
    """Sending for variant A on retailer X must not dedup variant B on retailer X."""
    shoe = _seed_novablast(db)
    _add_watch(db, shoe, threshold=100.0)
    _add_mapping(db, shoe, "running_warehouse")
    v_black = _add_variant(db, shoe, colorway_name="Black/Mint")
    v_yellow = _add_variant(db, shoe, colorway_name="Safety Yellow")
    # Black recently notified, yellow also qualifies and should win on min price.
    _snap(db, v_black, price=94.95)
    _snap(db, v_yellow, price=74.95)
    NotificationRepo(db).insert(NotificationRecord(
        user_id="me", shoe_variant_id=v_black.id, retailer="running_warehouse",
        triggering_price=94.95, sent_at=NOW - timedelta(days=1), channel="email",
    ))

    alerts = evaluate(db, now=NOW)
    assert len(alerts) == 1
    assert alerts[0].variant.colorway_name == "Safety Yellow"
