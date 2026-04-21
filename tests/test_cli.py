import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from shoe_tracker.adapters import ADAPTERS, RunningWarehouseAdapter
from shoe_tracker.cli import main


FIXTURES = Path(__file__).parent / "fixtures" / "running_warehouse"


class _StubClient:
    def __init__(self, responses: dict[str, str]):
        self._responses = responses

    def get(self, url: str) -> str:
        return self._responses[url]


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


def _run(runner: CliRunner, *args, db_path, config_path, input=None):
    return runner.invoke(
        main,
        ["--db", str(db_path), "--config", str(config_path), *args],
        input=input,
        catch_exceptions=False,
    )


def test_init_db_creates_file_and_syncs_rotation(tmp_path, fake_config):
    db_path = tmp_path / "t.db"
    runner = CliRunner()
    result = _run(runner, "init-db", db_path=db_path, config_path=fake_config)
    assert result.exit_code == 0, result.output
    assert db_path.exists()
    assert "Synced 1 rotation entry" in result.output


def test_rotation_list_shows_novablast_unmapped(tmp_path, fake_config):
    db_path = tmp_path / "t.db"
    runner = CliRunner()
    _run(runner, "init-db", db_path=db_path, config_path=fake_config)
    result = _run(runner, "rotation", "list", db_path=db_path, config_path=fake_config)
    assert result.exit_code == 0, result.output
    assert "ASICS Novablast 5" in result.output
    assert "M 10.5 D" in result.output
    assert "any colorway" in result.output
    assert "threshold $100" in result.output
    assert "unmapped" in result.output


def test_rotation_list_without_init_db_errors(tmp_path, fake_config):
    db_path = tmp_path / "missing.db"
    runner = CliRunner()
    result = _run(runner, "rotation", "list", db_path=db_path, config_path=fake_config)
    assert result.exit_code != 0
    assert "init-db" in result.output


def test_init_db_is_rerunnable(tmp_path, fake_config):
    db_path = tmp_path / "t.db"
    runner = CliRunner()
    r1 = _run(runner, "init-db", db_path=db_path, config_path=fake_config)
    r2 = _run(runner, "init-db", db_path=db_path, config_path=fake_config)
    assert r1.exit_code == 0
    assert r2.exit_code == 0
    # Second run should not duplicate the watchlist entry
    list_result = _run(runner, "rotation", "list", db_path=db_path, config_path=fake_config)
    assert list_result.output.count("ASICS Novablast 5") == 1


def test_probe_prints_variants_with_filters(monkeypatch, tmp_path, fake_config):
    # Load captured HTML for the mens search + one colorway page and wire a
    # stub HttpClient in. No network, fully deterministic.
    search_html = (FIXTURES / "search_mens_novablast.html").read_text()
    product_html = (FIXTURES / "product_anb5m1.html").read_text()
    # The stub returns the search page for ANY URL starting with /search-...,
    # and the one product page otherwise. Simpler than mirroring the real URLs.
    stub = _StubClient({})

    def fake_get(url: str) -> str:
        if "search-mens.html" in url:
            return search_html
        return product_html
    stub.get = fake_get  # type: ignore[method-assign]

    def factory():
        return RunningWarehouseAdapter(client=stub)
    monkeypatch.setitem(ADAPTERS, "running_warehouse", lambda: factory())  # type: ignore[arg-type]

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--db", str(tmp_path / "t.db"), "--config", str(fake_config),
         "probe", "running_warehouse",
         "--canonical", "ASICS Novablast 5", "--gender", "mens",
         "--size-min", "10", "--size-max", "11", "--width", "D"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert "Searched running_warehouse for ASICS Novablast 5" in result.output
    assert "10.5 / D" in result.output
    assert "in stock" in result.output
    assert "149.95" in result.output
    # Width filter excludes 2E rows
    assert "2E" not in result.output
