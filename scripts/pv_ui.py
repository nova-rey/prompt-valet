#!/usr/bin/env python3
"""Entrypoint for the NiceGUI-based Prompt Valet UI service."""

from __future__ import annotations

from nicegui import run

from prompt_valet.ui import UISettings, create_ui_app


def main() -> None:
    settings = UISettings.load()
    create_ui_app(settings)
    run(host=settings.ui_bind_host, port=settings.ui_bind_port)


if __name__ == "__main__":
    raise SystemExit(main())
