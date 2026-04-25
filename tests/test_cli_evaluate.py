"""Tests for `rotation evaluate` and `rotation set-threshold`.

The CLI wires the evaluator, notifier, and notifications_sent repo together.
Fixtures seed the DB so we don't need adapters or network.
"""
from __future__ import annotations

import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from shoe_tracker import cli as cli_mod
from shoe_tracker.cli import main
from shoe_tracker.db import (
    Database,
    NotificationRepo,
    PriceSnapshotRepo,
    RetailerMappingRepo,
    ShoeRepo,
    WatchlistRepo,
)
from shoe_tracker.models import (
    CanonicalShoe,
    PriceSnapshot,
    RetailerMapping,
    ShoeVariant,
)


@pytest.fixture
def fake_config(tmp_path):
    p = tmp_path / "rotation.yaml"
    p.write_text(textwrap.dedent("""
        user_email: me@example.com
        shoes:
          - brand: ASICS
            model: Novablast
            version: "5"
            gender: mens
            size: 10.5
            width: D
            colorway_policy: any
            threshold_usd: 100
    """))
    return p


def _run(runner: CliRunner, *args, db_path: Path, config_path: Path):
    return runner.invoke(
        main,
        ["--db", str(db_path), "--config", str(config_path), *args],
        catch_exceptions=False,
    )


def _init_and_seed(db_path: Path, config_path: Path) -> None:
    runner = CliRunner()
    result = _run(runner, "init-db", db_path=db_path, config_path=config_path)
    assert result.exit_code == 0, result.output


def _seed_variant_with_price(
    db_path: Path, *, price: float, in_stock: bool = True,
) -> tuple[CanonicalShoe, ShoeVariant]:
    with Database(db_path) as db:
        shoe = ShoeRepo(db).list_canonical()[0]
        RetailerMappingRepo(db).upsert(RetailerMapping(
            canonical_shoe_id=shoe.id, retailer="running_warehouse",
            product_url="https://rw/x", confidence=0.97,
        ))
        variant = ShoeRepo(db).upsert_variant(ShoeVariant(
            canonical_shoe_id=shoe.id, size=10.5, width="D",
            colorway_name="Black/Mint",
            image_url="https://cdn/x.jpg",
        ))
        PriceSnapshotRepo(db).insert(PriceSnapshot(
            shoe_variant_id=variant.id, retailer="running_warehouse",
            price_usd=price, in_stock=in_stock,
            scraped_at=datetime.now(timezone.utc),
            source_url="https://rw/x",
        ))
    return shoe, variant


# --- set-threshold ---

def test_set_threshold_updates_watchlist_entry(tmp_path, fake_config):
    db_path = tmp_path / "t.db"
    _init_and_seed(db_path, fake_config)
    runner = CliRunner()
    result = _run(
        runner, "rotation", "set-threshold", "Novablast 5", "999",
        db_path=db_path, config_path=fake_config,
    )
    assert result.exit_code == 0, result.output
    assert "999" in result.output
    with Database(db_path) as db:
        entry = WatchlistRepo(db).list_for_user()[0]
    assert entry.threshold_usd == 999.0


def test_set_threshold_unknown_shoe_errors(tmp_path, fake_config):
    db_path = tmp_path / "t.db"
    _init_and_seed(db_path, fake_config)
    runner = CliRunner()
    result = _run(
        runner, "rotation", "set-threshold", "Gel Kayano", "80",
        db_path=db_path, config_path=fake_config,
    )
    assert result.exit_code != 0
    assert "Gel Kayano" in result.output


# --- evaluate ---

def test_evaluate_dry_run_reports_alert_without_sending(tmp_path, fake_config, monkeypatch):
    db_path = tmp_path / "t.db"
    _init_and_seed(db_path, fake_config)
    _seed_variant_with_price(db_path, price=89.00)

    # If dry-run accidentally tried to build a notifier, this would raise.
    def _should_not_run():  # pragma: no cover
        raise AssertionError("notifier should not be created on dry run")
    monkeypatch.setattr(cli_mod, "email_notifier_from_env", _should_not_run)

    runner = CliRunner()
    result = _run(
        runner, "rotation", "evaluate", "--dry-run",
        db_path=db_path, config_path=fake_config,
    )
    assert result.exit_code == 0, result.output
    assert "ASICS Novablast 5" in result.output
    assert "89.00" in result.output
    assert "running_warehouse" in result.output

    with Database(db_path) as db:
        assert NotificationRepo(db).last_sent_at(
            user_id="me", shoe_variant_id=1, retailer="running_warehouse",
        ) is None


def test_evaluate_no_alerts_prints_clean_message(tmp_path, fake_config, monkeypatch):
    db_path = tmp_path / "t.db"
    _init_and_seed(db_path, fake_config)
    # Price above the $100 threshold — no alert.
    _seed_variant_with_price(db_path, price=150.00)

    monkeypatch.setattr(cli_mod, "email_notifier_from_env", lambda: None)
    runner = CliRunner()
    result = _run(
        runner, "rotation", "evaluate",
        db_path=db_path, config_path=fake_config,
    )
    assert result.exit_code == 0, result.output
    assert "No alerts" in result.output


def test_evaluate_sends_then_dedups(tmp_path, fake_config, monkeypatch):
    db_path = tmp_path / "t.db"
    _init_and_seed(db_path, fake_config)
    _seed_variant_with_price(db_path, price=89.00)

    sent_messages: list = []

    class _FakeNotifier:
        channel = "email"

        def notify(self, user, alert):
            sent_messages.append((user.email, alert.shoe.display_name, alert.price_usd))
            return True

    monkeypatch.setattr(cli_mod, "email_notifier_from_env", lambda: _FakeNotifier())

    runner = CliRunner()
    r1 = _run(runner, "rotation", "evaluate",
              db_path=db_path, config_path=fake_config)
    assert r1.exit_code == 0, r1.output
    assert len(sent_messages) == 1
    assert sent_messages[0][0] == "me@example.com"
    assert "email sent" in r1.output.lower()

    # A notification_sent row should exist now, so the second run dedups.
    r2 = _run(runner, "rotation", "evaluate",
              db_path=db_path, config_path=fake_config)
    assert r2.exit_code == 0, r2.output
    assert len(sent_messages) == 1  # not re-sent
    assert "No alerts" in r2.output


def test_evaluate_without_notifier_skips_send_but_reports(tmp_path, fake_config, monkeypatch):
    db_path = tmp_path / "t.db"
    _init_and_seed(db_path, fake_config)
    _seed_variant_with_price(db_path, price=89.00)

    monkeypatch.setattr(cli_mod, "email_notifier_from_env", lambda: None)
    runner = CliRunner()
    result = _run(
        runner, "rotation", "evaluate",
        db_path=db_path, config_path=fake_config,
    )
    assert result.exit_code == 0, result.output
    assert "ASICS Novablast 5" in result.output
    # Should surface the "no SMTP configured" condition.
    assert "SMTP" in result.output or "smtp" in result.output

    with Database(db_path) as db:
        # No dedup row written because nothing was actually sent.
        assert NotificationRepo(db).last_sent_at(
            user_id="me", shoe_variant_id=1, retailer="running_warehouse",
        ) is None


# --- prune ---

def test_rotation_prune_drops_old_snapshots(tmp_path, fake_config):
    db_path = tmp_path / "t.db"
    _init_and_seed(db_path, fake_config)
    with Database(db_path) as db:
        shoe = ShoeRepo(db).list_canonical()[0]
        variant = ShoeRepo(db).upsert_variant(ShoeVariant(
            canonical_shoe_id=shoe.id, size=10.5, width="D",
            colorway_name="Black/Mint",
        ))
        repo = PriceSnapshotRepo(db)
        repo.insert(PriceSnapshot(
            shoe_variant_id=variant.id, retailer="running_warehouse",
            price_usd=89.0, in_stock=True,
            scraped_at=datetime.now(timezone.utc) - timedelta(days=200),
            source_url="https://rw/x",
        ))
        repo.insert(PriceSnapshot(
            shoe_variant_id=variant.id, retailer="running_warehouse",
            price_usd=89.0, in_stock=True,
            scraped_at=datetime.now(timezone.utc),
            source_url="https://rw/x",
        ))

    runner = CliRunner()
    result = _run(
        runner, "rotation", "prune", "--days", "90",
        db_path=db_path, config_path=fake_config,
    )
    assert result.exit_code == 0, result.output
    assert "Pruned 1" in result.output
    with Database(db_path) as db:
        rows = db._conn.execute("SELECT COUNT(*) AS n FROM price_snapshots").fetchone()
    assert rows["n"] == 1


def test_rotation_prune_rejects_zero_or_negative_days(tmp_path, fake_config):
    db_path = tmp_path / "t.db"
    _init_and_seed(db_path, fake_config)
    runner = CliRunner()
    result = _run(
        runner, "rotation", "prune", "--days", "0",
        db_path=db_path, config_path=fake_config,
    )
    assert result.exit_code != 0


def test_evaluate_send_failure_does_not_write_dedup_row(tmp_path, fake_config, monkeypatch):
    db_path = tmp_path / "t.db"
    _init_and_seed(db_path, fake_config)
    _seed_variant_with_price(db_path, price=89.00)

    class _FailingNotifier:
        channel = "email"

        def notify(self, user, alert):
            return False

    monkeypatch.setattr(cli_mod, "email_notifier_from_env", lambda: _FailingNotifier())

    runner = CliRunner()
    result = _run(
        runner, "rotation", "evaluate",
        db_path=db_path, config_path=fake_config,
    )
    assert result.exit_code == 0, result.output
    assert "fail" in result.output.lower()
    with Database(db_path) as db:
        assert NotificationRepo(db).last_sent_at(
            user_id="me", shoe_variant_id=1, retailer="running_warehouse",
        ) is None
