import textwrap

import pytest
from click.testing import CliRunner

from shoe_tracker.cli import main


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
