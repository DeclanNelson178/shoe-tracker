from datetime import datetime, timezone

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
from shoe_tracker.models import (
    CanonicalShoe,
    NotificationRecord,
    PriceSnapshot,
    RetailerMapping,
    ShoeVariant,
    User,
    WatchlistEntry,
)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.db"
    init_db(path)
    d = Database(path)
    yield d
    d.close()


def test_init_db_is_idempotent(tmp_path):
    p = tmp_path / "t.db"
    init_db(p)
    init_db(p)  # second call must not error
    with Database(p) as db:
        row = db._conn.execute("SELECT COUNT(*) AS n FROM schema_version").fetchone()
    assert row["n"] >= 1


def test_user_upsert_and_fetch(db):
    repo = UserRepo(db)
    repo.upsert(User(id="me", email="me@example.com"))
    got = repo.get("me")
    assert got is not None
    assert got.email == "me@example.com"

    repo.upsert(User(id="me", email="new@example.com"))
    assert repo.get("me").email == "new@example.com"


def test_canonical_shoe_upsert_is_idempotent(db):
    repo = ShoeRepo(db)
    shoe = CanonicalShoe(
        brand="ASICS", model="Novablast", version="5",
        gender="mens", mfr_style_prefix="1011B867",
    )
    first = repo.upsert_canonical(shoe)
    second = repo.upsert_canonical(shoe)
    assert first.id == second.id
    assert repo.list_canonical()[0].id == first.id


def test_canonical_shoe_variant_type_distinguishes_rows(db):
    repo = ShoeRepo(db)
    plain = repo.upsert_canonical(CanonicalShoe(
        brand="Nike", model="Vomero", version="18", gender="mens",
    ))
    gtx = repo.upsert_canonical(CanonicalShoe(
        brand="Nike", model="Vomero", version="18", gender="mens", variant_type="GTX",
    ))
    assert plain.id != gtx.id


def test_watchlist_upsert_dedupes_by_user_shoe_size_width(db):
    UserRepo(db).upsert(User(id="me", email="me@example.com"))
    shoe = ShoeRepo(db).upsert_canonical(
        CanonicalShoe(brand="ASICS", model="Novablast", version="5", gender="mens")
    )
    repo = WatchlistRepo(db)
    e1 = repo.upsert(WatchlistEntry(
        canonical_shoe_id=shoe.id, size=10.5, width="D", threshold_usd=100,
    ))
    e2 = repo.upsert(WatchlistEntry(
        canonical_shoe_id=shoe.id, size=10.5, width="D", threshold_usd=85,
    ))
    assert e1.id == e2.id
    entries = repo.list_for_user()
    assert len(entries) == 1
    assert entries[0].threshold_usd == 85


def test_watchlist_persists_colorway_list(db):
    UserRepo(db).upsert(User(id="me", email="me@example.com"))
    shoe = ShoeRepo(db).upsert_canonical(
        CanonicalShoe(brand="ASICS", model="Novablast", version="5", gender="mens")
    )
    WatchlistRepo(db).upsert(WatchlistEntry(
        canonical_shoe_id=shoe.id, size=10.5, threshold_usd=100,
        colorway_policy="denylist", colorway_list=["Ugly", "Muddy"],
    ))
    got = WatchlistRepo(db).list_for_user()[0]
    assert got.colorway_policy == "denylist"
    assert got.colorway_list == ["Ugly", "Muddy"]


def test_retailer_mapping_upsert(db):
    shoe = ShoeRepo(db).upsert_canonical(
        CanonicalShoe(brand="ASICS", model="Novablast", version="5", gender="mens")
    )
    repo = RetailerMappingRepo(db)
    repo.upsert(RetailerMapping(
        canonical_shoe_id=shoe.id, retailer="running_warehouse",
        product_url="https://rw/x", confidence=0.97,
    ))
    repo.upsert(RetailerMapping(
        canonical_shoe_id=shoe.id, retailer="running_warehouse",
        product_url="https://rw/y", confidence=0.99,
    ))
    got = repo.get(shoe.id, "running_warehouse")
    assert got.product_url == "https://rw/y"
    assert got.confidence == 0.99


def test_price_snapshot_insert_and_latest(db):
    shoe = ShoeRepo(db).upsert_canonical(
        CanonicalShoe(brand="ASICS", model="Novablast", version="5", gender="mens")
    )
    variant = ShoeRepo(db).upsert_variant(ShoeVariant(
        canonical_shoe_id=shoe.id, size=10.5, width="D", colorway_name="Black/Mint",
    ))
    repo = PriceSnapshotRepo(db)
    ts1 = datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 4, 21, 12, 0, tzinfo=timezone.utc)
    repo.insert(PriceSnapshot(
        shoe_variant_id=variant.id, retailer="running_warehouse",
        price_usd=94.95, in_stock=True, scraped_at=ts1, source_url="https://x",
    ))
    repo.insert(PriceSnapshot(
        shoe_variant_id=variant.id, retailer="running_warehouse",
        price_usd=89.00, in_stock=True, scraped_at=ts2, source_url="https://x",
    ))
    latest = repo.latest_for_variant(variant.id)
    assert latest.price_usd == 89.00
    assert latest.scraped_at == ts2


def test_notification_last_sent_at(db):
    UserRepo(db).upsert(User(id="me", email="me@example.com"))
    shoe = ShoeRepo(db).upsert_canonical(
        CanonicalShoe(brand="ASICS", model="Novablast", version="5", gender="mens")
    )
    v = ShoeRepo(db).upsert_variant(ShoeVariant(
        canonical_shoe_id=shoe.id, size=10.5, width="D", colorway_name="Black/Mint",
    ))
    repo = NotificationRepo(db)
    ts = datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)
    repo.insert(NotificationRecord(
        user_id="me", shoe_variant_id=v.id, retailer="rw",
        triggering_price=89, sent_at=ts, channel="email",
    ))
    got = repo.last_sent_at(user_id="me", shoe_variant_id=v.id, retailer="rw")
    assert got == ts
    assert repo.last_sent_at(user_id="me", shoe_variant_id=v.id, retailer="other") is None
