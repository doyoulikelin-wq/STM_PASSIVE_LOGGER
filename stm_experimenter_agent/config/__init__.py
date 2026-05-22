"""Bundled YAML config loader."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

_CONFIG_DIR = Path(__file__).resolve().parent


def config_dir() -> Path:
    return _CONFIG_DIR


def load_yaml(name: str) -> Dict[str, Any]:
    """Load a bundled YAML config by file name (with or without .yaml)."""
    if not name.endswith((".yaml", ".yml")):
        name = name + ".yaml"
    path = _CONFIG_DIR / name
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Config {path} did not parse to a mapping")
    return data
