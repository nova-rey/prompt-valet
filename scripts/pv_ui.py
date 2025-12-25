#!/usr/bin/env python3
"""Entrypoint for the NiceGUI-based Prompt Valet UI service."""

from __future__ import annotations

from nicegui import ui

from prompt_valet.ui import UISettings, create_ui_app


def main() -> None:
    settings = UISettings.load()
    create_ui_app(settings)
    ui.run(host=settings.ui_bind_host, port=settings.ui_bind_port, reload=False, workers=1)


if __name__ in {"__main__", "__mp_main__"}:
    main()