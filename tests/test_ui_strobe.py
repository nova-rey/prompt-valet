from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict

import httpx
from nicegui import app as nicegui_app

import prompt_valet.ui.app as ui_app_module
from prompt_valet.ui import UISettings, create_ui_app
from tests.fixtures import stub_api


def _build_spy(label: Any) -> Dict[str, int]:
    counter: Dict[str, int] = {"calls": 0}
    original = label.set_text

    def _spy(value: Any) -> None:
        counter["calls"] += 1
        original(value)

    label.set_text = _spy
    return counter


def _delta(counter: Dict[str, int], snapshot: Dict[str, int]) -> int:
    return counter["calls"] - snapshot.get("calls", 0)


def test_ui_strobe_guard_limits_label_updates(monkeypatch) -> None:
    initial_routes = list(nicegui_app.router.routes)
    test_context: Dict[str, Dict[str, Any]] = {}
    transport_stub = httpx.ASGITransport(app=stub_api.create_stub_app())

    class StubPromptValetAPIClient(ui_app_module.PromptValetAPIClient):
        def __init__(self, base_url: str, timeout_seconds: float = 5.0, transport=None):
            super().__init__(
                base_url,
                timeout_seconds=timeout_seconds,
                transport=transport or transport_stub,
            )

    monkeypatch.setattr(ui_app_module, "PromptValetAPIClient", StubPromptValetAPIClient)
    fixed_now = datetime(2025, 1, 1, 0, 0, 0)

    class FixedDatetime(datetime):
        @classmethod
        def utcnow(cls) -> datetime:
            return fixed_now

    monkeypatch.setattr(ui_app_module, "datetime", FixedDatetime)

    settings = UISettings(
        api_base_url="http://stub/api/v1",
        ui_bind_host="0.0.0.0",
        ui_bind_port=8080,
        api_timeout_seconds=0.1,
    )

    try:
        create_ui_app(settings, test_context=test_context)
        services_panel = test_context["services_panel"]
        submit_panel = test_context["submit_panel"]

        connectivity_spy = _build_spy(services_panel["connectivity_hint_label"])
        watcher_spy = _build_spy(services_panel["watcher_status_detail"])
        target_spy = _build_spy(submit_panel["target_status_label"])

        refresh_services = services_panel["refresh_services"]
        refresh_targets = submit_panel["refresh_targets"]

        # First refresh should update each label at most once.
        snapshot_services = {
            "connectivity": connectivity_spy["calls"],
            "watcher": watcher_spy["calls"],
        }
        asyncio.run(refresh_services())
        assert _delta(connectivity_spy, snapshot_services) <= 1
        assert _delta(watcher_spy, snapshot_services) <= 1

        # Second refresh should not reapply the same labels more than once either.
        snapshot_services = {
            "connectivity": connectivity_spy["calls"],
            "watcher": watcher_spy["calls"],
        }
        asyncio.run(refresh_services())
        assert _delta(connectivity_spy, snapshot_services) <= 1
        assert _delta(watcher_spy, snapshot_services) <= 1

        # Repeat the same expectation for the target status label.
        snapshot_targets = {"target": target_spy["calls"]}
        asyncio.run(refresh_targets())
        assert _delta(target_spy, snapshot_targets) <= 1
        snapshot_targets = {"target": target_spy["calls"]}
        asyncio.run(refresh_targets())
        assert _delta(target_spy, snapshot_targets) <= 1
    finally:
        nicegui_app.router.routes[:] = initial_routes
