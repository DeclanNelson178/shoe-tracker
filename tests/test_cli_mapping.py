"""CLI tests for `rotation map` and `rotation status` (chunk 3)."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from shoe_tracker.adapters import ADAPTERS, RunningWarehouseAdapter
from shoe_tracker.cli import main


FIXTURES = Path(__file__).parent / "fixtures" / "running_warehouse"


class _StubClient:
    """Returns a preconfigured response for any URL that matches a key substring."""

    def __init__(self, routes: dict[str, str]):
        self._routes = routes
        self.calls: list[str] = []

    def get(self, url: str) -> str:
        self.calls.append(url)
        for key, body in self._routes.items():
            if key in url:
                return body
        raise AssertionError(f"unexpected URL: {url}")


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


@pytest.fixture
def stub_rw_adapter(monkeypatch):
    search_html = (FIXTURES / "search_mens_novablast.html").read_text()
    product_html = (FIXTURES / "product_anb5m1.html").read_text()
    stub = _StubClient({
        "search-mens.html": search_html,
        "search-womens.html": search_html,
        "descpage-": product_html,
    })

    monkeypatch.setitem(
        ADAPTERS, "running_warehouse",
        lambda: RunningWarehouseAdapter(client=stub),  # type: ignore[arg-type]
    )
    return stub


def _run(runner: CliRunner, *args, db_path, config_path):
    return runner.invoke(
        main,
        ["--db", str(db_path), "--config", str(config_path), *args],
        catch_exceptions=False,
    )


def test_rotation_map_writes_mapping_and_prints_confidence(
    tmp_path, fake_config, stub_rw_adapter,
):
    db_path = tmp_path / "t.db"
    review = tmp_path / "review.md"
    runner = CliRunner()
    _run(runner, "init-db", db_path=db_path, config_path=fake_config)
    result = _run(
        runner, "rotation", "map", "--retailer", "running_warehouse",
        "--review-path", str(review),
        db_path=db_path, config_path=fake_config,
    )
    assert result.exit_code == 0, result.output
    assert "Novablast 5" in result.output
    assert "mapped" in result.output
    assert "running_warehouse" in result.output
    # Confidence surfaces as "(0.xx)"
    assert "(" in result.output and ")" in result.output

    # Mapping is now persisted → rotation list reflects it.
    list_result = _run(
        runner, "rotation", "list", db_path=db_path, config_path=fake_config,
    )
    assert "running_warehouse(" in list_result.output, list_result.output
    assert "unmapped" not in list_result.output


def test_rotation_map_all_invokes_every_adapter(
    tmp_path, fake_config, stub_rw_adapter,
):
    db_path = tmp_path / "t.db"
    review = tmp_path / "review.md"
    runner = CliRunner()
    _run(runner, "init-db", db_path=db_path, config_path=fake_config)
    result = _run(
        runner, "rotation", "map", "--all", "--review-path", str(review),
        db_path=db_path, config_path=fake_config,
    )
    assert result.exit_code == 0, result.output
    # At minimum RW is registered; the --all path must touch it.
    assert "running_warehouse" in result.output


def test_rotation_map_requires_retailer_or_all(tmp_path, fake_config, stub_rw_adapter):
    db_path = tmp_path / "t.db"
    runner = CliRunner()
    _run(runner, "init-db", db_path=db_path, config_path=fake_config)
    result = _run(
        runner, "rotation", "map",
        db_path=db_path, config_path=fake_config,
    )
    assert result.exit_code != 0
    assert "retailer" in result.output.lower() or "--all" in result.output


def test_rotation_status_shows_current_min_price(
    tmp_path, fake_config, stub_rw_adapter,
):
    db_path = tmp_path / "t.db"
    review = tmp_path / "review.md"
    runner = CliRunner()
    _run(runner, "init-db", db_path=db_path, config_path=fake_config)
    _run(
        runner, "rotation", "map", "--retailer", "running_warehouse",
        "--review-path", str(review),
        db_path=db_path, config_path=fake_config,
    )
    result = _run(
        runner, "rotation", "status",
        db_path=db_path, config_path=fake_config,
    )
    assert result.exit_code == 0, result.output
    assert "Novablast 5" in result.output
    assert "threshold $100" in result.output
    # The stubbed product page is Gravel/White @ $149.95 — below-threshold test
    # isn't the point here; we just assert the status line renders with a price.
    assert "149.95" in result.output or "$149" in result.output
    assert "Gravel/White" in result.output
    assert "running_warehouse" in result.output or "@ rw" in result.output.lower()


def test_rotation_status_handles_unmapped(tmp_path, fake_config, stub_rw_adapter):
    db_path = tmp_path / "t.db"
    runner = CliRunner()
    _run(runner, "init-db", db_path=db_path, config_path=fake_config)
    result = _run(
        runner, "rotation", "status",
        db_path=db_path, config_path=fake_config,
    )
    assert result.exit_code == 0, result.output
    assert "Novablast 5" in result.output
    assert "unmapped" in result.output.lower() or "no price" in result.output.lower()
