"""Public API for the NiceGUI UI service."""

from __future__ import annotations

from prompt_valet.ui.app import create_ui_app
from prompt_valet.ui.settings import UISettings

__all__ = ["create_ui_app", "UISettings"]
