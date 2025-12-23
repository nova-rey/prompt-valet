"""Configuration helpers for the NiceGUI UI service."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_API_BASE_URL = "http://127.0.0.1:8888/api/v1"
DEFAULT_UI_BIND_HOST = "0.0.0.0"
DEFAULT_UI_BIND_PORT = 8080
DEFAULT_API_TIMEOUT_SECONDS = 5.0


def _parse_int_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _parse_float_env(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class UISettings:
    api_base_url: str
    ui_bind_host: str
    ui_bind_port: int
    api_timeout_seconds: float

    @classmethod
    def load(cls) -> UISettings:
        base_url = os.environ.get("PV_API_BASE_URL", DEFAULT_API_BASE_URL).rstrip("/")
        if not base_url:
            base_url = DEFAULT_API_BASE_URL
        return cls(
            api_base_url=base_url,
            ui_bind_host=os.environ.get("PV_UI_BIND_HOST", DEFAULT_UI_BIND_HOST),
            ui_bind_port=_parse_int_env("PV_UI_BIND_PORT", DEFAULT_UI_BIND_PORT),
            api_timeout_seconds=_parse_float_env(
                "PV_UI_API_TIMEOUT_SECONDS", DEFAULT_API_TIMEOUT_SECONDS
            ),
        )
