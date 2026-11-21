"""YAML 配置加载（带缓存），配置改动后可通过 reload 重新读取。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.core.config import CONFIG_DIR

_cache: dict[str, tuple[float, Any]] = {}


def load_yaml(name: str, force: bool = False) -> dict:
    path: Path = CONFIG_DIR / name
    if not path.exists():
        return {}
    mtime = path.stat().st_mtime
    cached = _cache.get(name)
    if cached and not force and cached[0] == mtime:
        return cached[1]
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    _cache[name] = (mtime, data)
    return data


def reload_all() -> None:
    for name in list(_cache):
        load_yaml(name, force=True)


def asset_classes_config() -> dict:
    return load_yaml("asset_classes.yaml")


def instruments_config() -> dict:
    return load_yaml("instruments.yaml")


def data_sources_config() -> dict:
    return load_yaml("data_sources.yaml")


def signal_rules() -> dict:
    return load_yaml("signal_rules.yaml")


def risk_limits() -> dict:
    return load_yaml("risk_limits.yaml")


def config_version() -> str:
    rules = signal_rules()
    return f"{rules.get('version', 0)}.{risk_limits().get('version', 0)}"
