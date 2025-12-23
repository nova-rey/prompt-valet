"""NiceGUI UI layout for the Prompt Valet control plane."""

from __future__ import annotations

from nicegui import ui

from prompt_valet.ui.client import PromptValetAPIClient
from prompt_valet.ui.settings import UISettings


def _style_card(title: str, body: str) -> None:
    ui.label(title).classes("font-semibold text-base")
    ui.label(body).classes("text-sm text-gray-600")


def _build_dashboard_panel(settings: UISettings) -> None:
    ui.markdown(f"**API base:** `{settings.api_base_url}`")
    with ui.row().classes("flex-wrap gap-4 pt-2"):
        with ui.card().classes("w-full sm:w-1/2 lg:w-1/3"):
            _style_card(
                "Connectivity",
                "Live API status lives in the header. The indicator reflects the last `healthz` check.",
            )
        with ui.card().classes("w-full sm:w-1/2 lg:w-1/3"):
            _style_card(
                "Dashboard tabs",
                "This placeholder will show job summaries and trends once the UI is wired to the control plane.",
            )
        with ui.card().classes("w-full lg:w-1/3"):
            _style_card(
                "Operational notes",
                "The UI does not read or write job files; it only talks to `/api/v1/*` and mirrors the control plane state.",
            )


def _build_submit_panel() -> None:
    ui.markdown("### Submit placeholder")
    ui.markdown(
        "Describe how submission will look once connected. For now this tab warns that the UI just mirrors API functionality."
    )
    with ui.card().classes("mt-4 w-full sm:w-3/4 lg:w-1/2"):
        ui.label("Job submission work will live here.").classes("font-medium")
        ui.label(
            "Connects to `/api/v1/jobs` and `/api/v1/jobs/upload` in the future."
        ).classes("text-sm text-gray-600")


def _build_services_panel() -> None:
    ui.markdown("### Services overview")
    ui.markdown(
        "Reserved for service health cards, rollout actions, or configuration helpers once they are defined."
    )
    with ui.card().classes("mt-4 w-full lg:w-2/3"):
        ui.label("Service wiring").classes("font-medium")
        ui.label(
            "This panel only displays static text until more endpoints are exposed."
        ).classes("text-sm text-gray-600")


def create_ui_app(settings: UISettings | None = None) -> None:
    settings = settings or UISettings.load()
    client = PromptValetAPIClient(
        settings.api_base_url, timeout_seconds=settings.api_timeout_seconds
    )

    with ui.header().classes("justify-between px-6"):
        ui.label("Prompt Valet UI").classes("text-lg font-semibold")
        with ui.row().classes("items-center gap-3"):
            status_icon = ui.icon("cloud").classes("text-xl text-gray-400")
            status_label = ui.label("Checking API...").classes(
                "font-medium text-gray-500"
            )

    async def refresh_connectivity() -> None:
        report = await client.ping()
        if report.reachable:
            color = "text-emerald-500"
            status_label.set_text(
                f"API reachable (v{report.version})"
                if report.version
                else "API reachable"
            )
        else:
            color = "text-red-500"
            detail = f" ({report.detail})" if report.detail else ""
            status_label.set_text(f"API unreachable{detail}")
        status_icon.set_classes(f"text-xl {color}")
        status_label.set_classes(f"font-medium {color}")

    ui.timer(5, refresh_connectivity, on_start=True)

    with ui.tabs().classes("w-full").props("pills"):
        ui.tab("Dashboard")
        ui.tab("Submit")
        ui.tab("Services")

    with ui.tab_panels():
        with ui.tab_panel("Dashboard"):
            _build_dashboard_panel(settings)
        with ui.tab_panel("Submit"):
            _build_submit_panel()
        with ui.tab_panel("Services"):
            _build_services_panel()
