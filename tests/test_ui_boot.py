"""Smoke test that ensures the NiceGUI UI entrypoint boots without errors."""

from nicegui import app

from prompt_valet.ui import UISettings, create_ui_app


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
