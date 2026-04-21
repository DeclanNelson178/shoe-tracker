import textwrap

import pytest

from shoe_tracker.config import ConfigError, load_rotation


def test_loads_packaged_rotation_yaml():
    cfg = load_rotation("config/rotation.yaml")
    assert cfg.user_email
    assert len(cfg.shoes) >= 1
    novablast = cfg.shoes[0]
    assert novablast.brand == "ASICS"
    assert novablast.model == "Novablast"
    assert novablast.version == "5"
    assert novablast.size == 10.5
    assert novablast.width == "D"
    assert novablast.threshold_usd == 100


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_rotation(tmp_path / "nope.yaml")


def test_invalid_yaml_raises(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(textwrap.dedent("""
        user_email: me@example.com
        shoes:
          - brand: ASICS
            model: Novablast
            gender: mens
            size: 10.5
            threshold_usd: -5
    """))
    with pytest.raises(ConfigError):
        load_rotation(p)


def test_empty_file_raises(tmp_path):
    p = tmp_path / "empty.yaml"
    p.write_text("")
    with pytest.raises(ConfigError):
        load_rotation(p)
