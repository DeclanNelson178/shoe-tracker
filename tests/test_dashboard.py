"""Tests for the dashboard view-model + HTML/JSON rendering."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from shoe_tracker.dashboard import (
    build,
    render_html,
    render_json,
)
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
from shoe_tracker.models import (
    CanonicalShoe,
    NotificationRecord,
    PriceSnapshot,
    RetailerMapping,
    ShoeVariant,
    User,
    WatchlistEntry,
)

NOW = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path):
    init_db(tmp_path / "t.db")
    d = Database(tmp_path / "t.db")
    UserRepo(d).upsert(User(id="me", email="me@example.com"))
    yield d
    d.close()


def _seed_shoe(db) -> CanonicalShoe:
    return ShoeRepo(db).upsert_canonical(
        CanonicalShoe(brand="ASICS", model="Novablast", version="5", gender="mens")
    )


def _seed_watchlist(
    db, shoe, *, threshold=100.0, policy="any", colorway_list=(),
) -> WatchlistEntry:
    return WatchlistRepo(db).upsert(WatchlistEntry(
        canonical_shoe_id=shoe.id, size=10.5, width="D",
        threshold_usd=threshold,
        colorway_policy=policy,
        colorway_list=list(colorway_list),
    ))


def _seed_mapping(db, shoe, retailer: str, *, url="https://retailer/x") -> None:
    RetailerMappingRepo(db).upsert(RetailerMapping(
        canonical_shoe_id=shoe.id, retailer=retailer,
        product_url=url, confidence=0.95,
    ))


def _seed_snapshot(
    db, shoe, *,
    retailer: str, price: float, in_stock: bool = True,
    colorway: str = "Black/Mint", colorway_code: str | None = None,
    image_url: str | None = "https://cdn/x.jpg",
    scraped_at: datetime | None = None,
) -> ShoeVariant:
    variant = ShoeRepo(db).upsert_variant(ShoeVariant(
        canonical_shoe_id=shoe.id, size=10.5, width="D",
        colorway_name=colorway, colorway_code=colorway_code,
        image_url=image_url,
    ))
    PriceSnapshotRepo(db).insert(PriceSnapshot(
        shoe_variant_id=variant.id, retailer=retailer,
        price_usd=price, in_stock=in_stock,
        scraped_at=scraped_at or NOW - timedelta(hours=1),
        source_url=f"https://{retailer}/p",
    ))
    return variant


# --- build() ----------------------------------------------------------------

def test_build_empty_rotation_marks_stale_with_no_data(db):
    data = build(db, now=NOW)
    assert data.entries == []
    assert data.alerts == []
    assert data.last_scrape_at is None
    assert data.is_stale is True


def test_build_picks_cheapest_in_stock_across_retailers(db):
    shoe = _seed_shoe(db)
    _seed_watchlist(db, shoe, threshold=100.0)
    for r in ("running_warehouse", "holabird", "rrs"):
        _seed_mapping(db, shoe, r, url=f"https://{r}/x")
    _seed_snapshot(db, shoe, retailer="running_warehouse", price=94.95)
    _seed_snapshot(db, shoe, retailer="holabird", price=89.00)
    _seed_snapshot(db, shoe, retailer="rrs", price=109.00, in_stock=False)

    data = build(db, now=NOW)
    assert len(data.entries) == 1
    e = data.entries[0]
    assert e.headline is not None
    assert e.headline.price_usd == 89.00
    assert e.headline.retailer == "holabird"
    assert e.state == "below"
    # All three retailers represented in the breakdown.
    assert {r.retailer for r in e.rows} == {"running_warehouse", "holabird", "rrs"}


def test_build_state_near_when_within_ten_percent(db):
    shoe = _seed_shoe(db)
    _seed_watchlist(db, shoe, threshold=100.0)
    _seed_mapping(db, shoe, "running_warehouse")
    _seed_snapshot(db, shoe, retailer="running_warehouse", price=105.00)
    data = build(db, now=NOW)
    assert data.entries[0].state == "near"


def test_build_state_above_when_far_above_threshold(db):
    shoe = _seed_shoe(db)
    _seed_watchlist(db, shoe, threshold=100.0)
    _seed_mapping(db, shoe, "running_warehouse")
    _seed_snapshot(db, shoe, retailer="running_warehouse", price=140.00)
    data = build(db, now=NOW)
    assert data.entries[0].state == "above"


def test_build_state_no_data_when_no_in_stock_match(db):
    shoe = _seed_shoe(db)
    _seed_watchlist(db, shoe, threshold=100.0)
    _seed_mapping(db, shoe, "running_warehouse")
    _seed_snapshot(db, shoe, retailer="running_warehouse", price=89.00, in_stock=False)
    data = build(db, now=NOW)
    e = data.entries[0]
    assert e.headline is None
    assert e.state == "no_data"
    assert len(e.rows) == 1
    assert e.rows[0].in_stock is False


def test_build_allowlist_excludes_unlisted_colorways_from_headline(db):
    shoe = _seed_shoe(db)
    _seed_watchlist(
        db, shoe, threshold=100.0,
        policy="allowlist", colorway_list=["Black/Mint"],
    )
    _seed_mapping(db, shoe, "running_warehouse")
    # Cheaper option is wrong colorway -> shouldn't drive the headline.
    _seed_snapshot(db, shoe, retailer="running_warehouse",
                   price=70.00, colorway="Safety Yellow")
    _seed_snapshot(db, shoe, retailer="running_warehouse",
                   price=89.00, colorway="Black/Mint")
    data = build(db, now=NOW)
    e = data.entries[0]
    assert e.headline is not None
    assert e.headline.colorway_name == "Black/Mint"
    assert e.headline.price_usd == 89.00
    # Both rows are surfaced; the unlisted one is flagged.
    by_color = {r.colorway_name: r for r in e.rows}
    assert by_color["Safety Yellow"].matches_policy is False
    assert by_color["Black/Mint"].matches_policy is True


def test_build_marks_stale_when_last_scrape_old(db):
    shoe = _seed_shoe(db)
    _seed_watchlist(db, shoe, threshold=100.0)
    _seed_mapping(db, shoe, "running_warehouse")
    _seed_snapshot(
        db, shoe, retailer="running_warehouse", price=89.00,
        scraped_at=NOW - timedelta(hours=40),
    )
    data = build(db, now=NOW)
    assert data.is_stale is True


def test_build_not_stale_when_recent_scrape(db):
    shoe = _seed_shoe(db)
    _seed_watchlist(db, shoe, threshold=100.0)
    _seed_mapping(db, shoe, "running_warehouse")
    _seed_snapshot(
        db, shoe, retailer="running_warehouse", price=89.00,
        scraped_at=NOW - timedelta(hours=2),
    )
    data = build(db, now=NOW)
    assert data.is_stale is False


def test_build_alert_history_filters_to_last_thirty_days(db):
    shoe = _seed_shoe(db)
    _seed_watchlist(db, shoe, threshold=100.0)
    variant = ShoeRepo(db).upsert_variant(ShoeVariant(
        canonical_shoe_id=shoe.id, size=10.5, width="D",
        colorway_name="Black/Mint",
    ))
    repo = NotificationRepo(db)
    for days_ago, retailer in [(1, "rw"), (10, "holabird"), (40, "old")]:
        repo.insert(NotificationRecord(
            user_id="me", shoe_variant_id=variant.id, retailer=retailer,
            triggering_price=89.0, sent_at=NOW - timedelta(days=days_ago),
            channel="email",
        ))

    data = build(db, now=NOW)
    assert [a.retailer for a in data.alerts] == ["rw", "holabird"]
    assert all(a.shoe_display == "ASICS Novablast 5" for a in data.alerts)
    assert all(a.colorway_name == "Black/Mint" for a in data.alerts)


# --- render_html() ----------------------------------------------------------

def test_render_html_includes_entry_data_and_breakdown(db):
    shoe = _seed_shoe(db)
    _seed_watchlist(db, shoe, threshold=100.0)
    _seed_mapping(db, shoe, "running_warehouse")
    _seed_snapshot(db, shoe, retailer="running_warehouse", price=89.00,
                   colorway="Black/Mint")
    html = render_html(build(db, now=NOW))

    assert "ASICS Novablast 5" in html
    assert "$89.00" in html
    assert "$100.00" in html
    assert "Black/Mint" in html
    assert "running_warehouse" in html
    # Visual state class is wired up.
    assert "state-below" in html
    # Mobile viewport is set.
    assert 'name="viewport"' in html
    # Threshold delta surfaced as a "save" marker.
    assert "save" in html.lower()


def test_render_html_shows_stale_banner_when_data_is_stale(db):
    shoe = _seed_shoe(db)
    _seed_watchlist(db, shoe, threshold=100.0)
    # No snapshots → is_stale True.
    html = render_html(build(db, now=NOW))
    assert "stale" in html.lower()


def test_render_html_handles_empty_rotation(db):
    html = render_html(build(db, now=NOW))
    assert "shoe-tracker" in html
    assert "No watchlist entries" in html


def test_render_html_recent_alerts_section(db):
    shoe = _seed_shoe(db)
    _seed_watchlist(db, shoe, threshold=100.0)
    variant = ShoeRepo(db).upsert_variant(ShoeVariant(
        canonical_shoe_id=shoe.id, size=10.5, width="D",
        colorway_name="Black/Mint",
    ))
    NotificationRepo(db).insert(NotificationRecord(
        user_id="me", shoe_variant_id=variant.id, retailer="holabird",
        triggering_price=89.00, sent_at=NOW - timedelta(days=2),
        channel="email",
    ))
    html = render_html(build(db, now=NOW))
    assert "Recent alerts" in html
    assert "holabird" in html


# --- render_json() ----------------------------------------------------------

def test_render_json_round_trips(db):
    shoe = _seed_shoe(db)
    _seed_watchlist(db, shoe, threshold=100.0)
    _seed_mapping(db, shoe, "running_warehouse")
    _seed_snapshot(db, shoe, retailer="running_warehouse", price=89.00)
    payload = render_json(build(db, now=NOW))
    parsed = json.loads(payload)
    assert "entries" in parsed
    assert parsed["entries"][0]["headline"]["price_usd"] == 89.00
    assert parsed["entries"][0]["state"] == "below"
    assert parsed["is_stale"] is False
