"""Tests for the scripts/render_dashboard.py CLI entry point.

The script is a thin wrapper around `shoe_tracker.dashboard.render_to_dir`.
These tests verify the CLI plumbing (argparse, missing-DB handling, file
emission); the rendering itself is covered by tests/test_dashboard.py.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from render_dashboard import main

from shoe_tracker.db import (
    Database,
    PriceSnapshotRepo,
    RetailerMappingRepo,
    ShoeRepo,
    UserRepo,
    WatchlistRepo,
    init_db,
)
from shoe_tracker.models import (
    CanonicalShoe,
    PriceSnapshot,
    RetailerMapping,
    ShoeVariant,
    User,
    WatchlistEntry,
)


def _seed(db_path) -> None:
    init_db(db_path)
    with Database(db_path) as db:
        UserRepo(db).upsert(User(id="me", email="me@example.com"))
        shoe = ShoeRepo(db).upsert_canonical(CanonicalShoe(
            brand="ASICS", model="Novablast", version="5", gender="mens",
        ))
        WatchlistRepo(db).upsert(WatchlistEntry(
            canonical_shoe_id=shoe.id, size=10.5, width="D",
            threshold_usd=100.0,
        ))
        RetailerMappingRepo(db).upsert(RetailerMapping(
            canonical_shoe_id=shoe.id, retailer="running_warehouse",
            product_url="https://rw/x", confidence=0.97,
        ))
        variant = ShoeRepo(db).upsert_variant(ShoeVariant(
            canonical_shoe_id=shoe.id, size=10.5, width="D",
            colorway_name="Black/Mint",
        ))
        PriceSnapshotRepo(db).insert(PriceSnapshot(
            shoe_variant_id=variant.id, retailer="running_warehouse",
            price_usd=89.00, in_stock=True,
            scraped_at=datetime(2026, 4, 25, 13, 0, tzinfo=timezone.utc),
            source_url="https://rw/x",
        ))


def test_main_writes_index_html_and_data_json(tmp_path):
    db_path = tmp_path / "t.db"
    out_dir = tmp_path / "docs"
    _seed(db_path)

    code = main(["--db", str(db_path), "--out", str(out_dir)])
    assert code == 0

    index = out_dir / "index.html"
    data = out_dir / "data.json"
    assert index.exists()
    assert data.exists()

    html = index.read_text()
    assert "ASICS Novablast 5" in html
    assert "$89.00" in html

    parsed = json.loads(data.read_text())
    assert parsed["entries"][0]["headline"]["price_usd"] == 89.00


def test_main_no_db_returns_zero_and_writes_nothing(tmp_path, capsys):
    out_dir = tmp_path / "docs"
    code = main(["--db", str(tmp_path / "missing.db"), "--out", str(out_dir)])
    assert code == 0
    out = capsys.readouterr().out
    assert "DB not found" in out
    assert not out_dir.exists() or not (out_dir / "index.html").exists()
