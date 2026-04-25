"""rotation.yaml loader."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from .models import RotationConfig

DEFAULT_PATH = Path("config/rotation.yaml")


class ConfigError(Exception):
    pass


def load_rotation(path: str | Path = DEFAULT_PATH) -> RotationConfig:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"rotation config not found: {p}")
    raw = yaml.safe_load(p.read_text())
    if raw is None:
        raise ConfigError(f"rotation config is empty: {p}")
    try:
        return RotationConfig.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(f"rotation config invalid: {p}\n{e}") from e
