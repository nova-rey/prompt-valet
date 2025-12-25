"""Smoke test that ensures the NiceGUI UI entrypoint boots without errors."""

from nicegui import app

from prompt_valet.ui import UISettings, create_ui_app
from prompt_valet.ui.app import (
    _set_classes_if_changed,
    _set_text_if_changed,
    _set_visibility_if_changed,
)


def test_ui_constructs_tabs() -> None:
    """Importing the UI module and creating the app should register NiceGUI routes."""

    initial_routes = list(app.router.routes)
    settings = UISettings(
        api_base_url="http://127.0.0.1:8888/api/v1",
        ui_bind_host="0.0.0.0",
        ui_bind_port=8080,
        api_timeout_seconds=0.1,
    )

    try:
        create_ui_app(settings)
        new_paths = {route.path for route in app.router.routes}
        original_paths = {route.path for route in initial_routes}
        added_paths = new_paths - original_paths
        assert added_paths, "UI creation should add NiceGUI routes"
        assert any(path.startswith("/_nicegui/client/") for path in added_paths)
    finally:
        app.router.routes[:] = initial_routes


class DummyElement:
    def __init__(self) -> None:
        self.text_calls = 0
        self.classes_calls = 0
        self.visibility_calls = 0

    def set_text(self, _) -> None:
        self.text_calls += 1

    def classes(self, _) -> "DummyElement":
        self.classes_calls += 1
        return self

    def set_visibility(self, _) -> None:
        self.visibility_calls += 1


def test_set_text_if_changed_gates_updates() -> None:
    dummy = DummyElement()
    _set_text_if_changed(dummy, "alpha")
    _set_text_if_changed(dummy, "alpha")
    assert dummy.text_calls == 1
    _set_text_if_changed(dummy, "beta")
    assert dummy.text_calls == 2


def test_set_classes_if_changed_gates_updates() -> None:
    dummy = DummyElement()
    _set_classes_if_changed(dummy, "foo")
    _set_classes_if_changed(dummy, "foo")
    assert dummy.classes_calls == 1
    _set_classes_if_changed(dummy, "bar")
    assert dummy.classes_calls == 2


def test_set_visibility_if_changed_gates_updates() -> None:
    dummy = DummyElement()
    _set_visibility_if_changed(dummy, True)
    _set_visibility_if_changed(dummy, True)
    assert dummy.visibility_calls == 1
    _set_visibility_if_changed(dummy, False)
    assert dummy.visibility_calls == 2
