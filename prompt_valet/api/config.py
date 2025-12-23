"""Configuration helpers for the Prompt Valet API."""

from __future__ import annotations

import copy
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from scripts.codex_watcher import (
    DEFAULT_CONFIG,
    DEFAULT_CONFIG_PATH,
    DEFAULT_PV_ROOT,
    RUNS_DIR_NAME,
    normalize_config,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_STALL_THRESHOLD_SECONDS = 60
DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_BIND_PORT = 8888


@dataclass(frozen=True)
class APISettings:
    tree_builder_root: Path
    runs_root: Path
    stall_threshold_seconds: int
    bind_host: str
    bind_port: int
    git_owner: str
    inbox_mode: str

    @classmethod
    def load(cls) -> APISettings:
        config_path = Path(os.environ.get("PV_CONFIG_PATH", DEFAULT_CONFIG_PATH))
        cfg: dict[str, Any] = copy.deepcopy(DEFAULT_CONFIG)
        if config_path.is_file():
            try:
                user_cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
                if not isinstance(user_cfg, dict):
                    raise ValueError(
                        "configuration file must contain a mapping at the top level"
                    )
                for key, value in user_cfg.items():
                    if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                        cfg[key].update(value)
                    else:
                        cfg[key] = value
            except Exception as exc:  # pragma: no cover - defensive
                _LOGGER.warning("Failed to load config at %s: %s", config_path, exc)
        else:
            _LOGGER.debug(
                "Config file %s missing; falling back to defaults", config_path
            )

        cfg = normalize_config(cfg)
        pv_root = Path(cfg.get("pv_root", str(DEFAULT_PV_ROOT))).expanduser().resolve()
        tree_builder_root = (
            Path(
                os.environ.get(
                    "PV_REPOS_ROOT", cfg.get("inbox", str(DEFAULT_PV_ROOT / "inbox"))
                )
            )
            .expanduser()
            .resolve()
        )
        runs_root = (
            Path(os.environ.get("PV_RUNS_ROOT", str(pv_root / RUNS_DIR_NAME)))
            .expanduser()
            .resolve()
        )
        stall_threshold = _parse_int_env(
            "PV_STALL_THRESHOLD_SECONDS", DEFAULT_STALL_THRESHOLD_SECONDS
        )
        bind_host = os.environ.get("PV_BIND_HOST", DEFAULT_BIND_HOST)
        bind_port = _parse_int_env("PV_BIND_PORT", DEFAULT_BIND_PORT)

        return cls(
            tree_builder_root=tree_builder_root,
            runs_root=runs_root,
            stall_threshold_seconds=stall_threshold,
            bind_host=bind_host,
            bind_port=bind_port,
            git_owner=cfg.get("git_owner", "nova-rey"),
            inbox_mode=cfg.get("inbox_mode", "legacy_single_owner"),
        )


def _parse_int_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        _LOGGER.warning("Invalid integer for %s: %r; using %s", name, value, default)
        return default


@lru_cache(maxsize=1)
def get_api_settings() -> APISettings:
    return APISettings.load()


__all__ = ["APISettings", "get_api_settings"]
