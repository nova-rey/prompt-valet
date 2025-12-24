"""NiceGUI UI layout for the Prompt Valet control plane."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from nicegui import ui

from prompt_valet.ui.client import PromptValetAPIClient
from prompt_valet.ui.settings import UISettings

TIMESTAMP_FIELDS = (
    "created_at",
    "started_at",
    "updated_at",
    "heartbeat_at",
    "finished_at",
)

_STATE_BADGE_STYLES: Dict[str, str] = {
    "queued": "bg-slate-100 text-slate-700",
    "running": "bg-blue-100 text-blue-700",
    "succeeded": "bg-emerald-100 text-emerald-700",
    "failed": "bg-rose-100 text-rose-700",
    "aborted": "bg-stone-100 text-stone-700",
    "unknown": "bg-amber-100 text-amber-700",
}

TERMINAL_STATES = {"succeeded", "failed", "aborted"}


def _style_card(title: str, body: str) -> None:
    ui.label(title).classes("font-semibold text-base")
    ui.label(body).classes("text-sm text-gray-600")


def _parse_iso_timestamp(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _format_timestamp(value: Any) -> Optional[str]:
    parsed = _parse_iso_timestamp(value)
    if parsed is None:
        return None
    return parsed.strftime("%Y-%m-%d %H:%M:%S UTC")


def _format_timestamp_label(label: str, value: Any) -> Optional[str]:
    formatted = _format_timestamp(value)
    if not formatted:
        return None
    return f"{label}: {formatted}"


def _format_relative_age(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h"
    days = hours // 24
    return f"{days}d"


def _normalize_state(state: str | None) -> str:
    if not state:
        return "unknown"
    lowered = state.lower()
    if lowered in {"failed_final", "failed_retryable"}:
        return "failed"
    if lowered in {"succeeded", "running", "queued", "aborted"}:
        return lowered
    return lowered


def _format_state_badge(state: str, stalled: bool) -> tuple[str, str]:
    text = state.capitalize()
    if state == "running" and stalled:
        text = "Running (stalled)"
    classes = _STATE_BADGE_STYLES.get(state, _STATE_BADGE_STYLES["unknown"])
    return text, classes


def _repo_display(job: Dict[str, Any]) -> str:
    owner = job.get("git_owner") or ""
    repo = job.get("repo_name") or ""
    if owner and repo:
        return f"{owner}/{repo}"
    if repo:
        return repo
    if owner:
        return owner
    return "—"


def _format_time_cell(job: Dict[str, Any]) -> str:
    pieces: List[str] = []
    created = _format_timestamp(job.get("created_at"))
    if created:
        pieces.append(f"Created {created}")
    started = _format_timestamp(job.get("started_at"))
    if started:
        pieces.append(f"Started {started}")
    return " • ".join(pieces) if pieces else "—"


def _format_heartbeat_cell(job: Dict[str, Any]) -> str:
    if job.get("stalled"):
        return "⚠ Stalled"
    heartbeat = _parse_iso_timestamp(job.get("heartbeat_at"))
    if heartbeat:
        delta = datetime.utcnow() - heartbeat
        return f"HB {_format_relative_age(delta)} ago"
    return "—"


def _sort_key_for_job(job: Dict[str, Any]) -> datetime:
    for field in ("created_at", "started_at", "updated_at", "heartbeat_at"):
        parsed = _parse_iso_timestamp(job.get(field))
        if parsed is not None:
            return parsed
    return datetime.utcfromtimestamp(0)


def _build_job_rows(
    jobs: List[Dict[str, Any]], *, descending: bool
) -> List[Dict[str, Any]]:
    sorted_jobs = sorted(jobs, key=_sort_key_for_job, reverse=descending)
    rows: List[Dict[str, Any]] = []
    for job in sorted_jobs:
        state_norm = _normalize_state(job.get("state"))
        state_display = state_norm.capitalize()
        if state_norm == "running" and job.get("stalled"):
            state_display = "Running (stalled)"
        is_terminal = state_norm in TERMINAL_STATES
        exit_code = job.get("exit_code")
        rows.append(
            {
                "job_id": job.get("job_id") or "—",
                "repo": _repo_display(job),
                "branch": job.get("branch_name") or "—",
                "state": state_display,
                "time": _format_time_cell(job),
                "heartbeat": _format_heartbeat_cell(job),
                "exit_code": (
                    str(exit_code) if is_terminal and exit_code is not None else "—"
                ),
            }
        )
    return rows


def _build_dashboard_panel(settings: UISettings, client: PromptValetAPIClient) -> None:
    jobs_data: List[Dict[str, Any]] = []
    sort_descending = True
    refresh_in_progress = False

    detail_dialog: Optional[Any] = None
    detail_title: Optional[Any] = None
    detail_subtitle: Optional[Any] = None
    detail_state_badge: Optional[Any] = None
    detail_stalled_label: Optional[Any] = None
    detail_age_label: Optional[Any] = None
    detail_error_label: Optional[Any] = None
    detail_loading_label: Optional[Any] = None
    detail_timestamp_labels: Dict[str, Any] = {}
    detail_metadata_table: Optional[Any] = None

    jobs_table: Optional[Any] = None
    refresh_button: Optional[Any] = None
    sort_button: Optional[Any] = None
    jobs_error_label: Optional[Any] = None
    jobs_loading_label: Optional[Any] = None
    jobs_empty_label: Optional[Any] = None

    def _update_jobs_table() -> None:
        if jobs_table is None:
            return
        rows = _build_job_rows(jobs_data, descending=sort_descending)
        jobs_table.rows = rows
        if jobs_empty_label is not None:
            jobs_empty_label.visible = not bool(rows)

    async def _refresh_jobs() -> None:
        nonlocal refresh_in_progress, jobs_data
        if refresh_in_progress:
            return
        refresh_in_progress = True
        if refresh_button is not None:
            refresh_button.disabled = True
        if jobs_loading_label is not None:
            jobs_loading_label.visible = True
        try:
            jobs_data = await client.list_jobs()
            if jobs_error_label is not None:
                jobs_error_label.visible = False
            _update_jobs_table()
        except Exception as exc:  # noqa: BLE001
            if jobs_error_label is not None:
                jobs_error_label.set_text(f"Failed to load jobs: {exc}")
                jobs_error_label.visible = True
        finally:
            refresh_in_progress = False
            if refresh_button is not None:
                refresh_button.disabled = False
            if jobs_loading_label is not None:
                jobs_loading_label.visible = False

    def _toggle_sort() -> None:
        nonlocal sort_descending
        sort_descending = not sort_descending
        if sort_button is not None:
            label = "Created ↓" if sort_descending else "Created ↑"
            sort_button.set_text(label)
        _update_jobs_table()

    def _render_job_detail(job: Dict[str, Any]) -> None:
        if (
            detail_title is None
            or detail_subtitle is None
            or detail_state_badge is None
        ):
            return
        job_id = job.get("job_id") or "—"
        detail_title.set_text(f"Job {job_id}")
        repo_label = _repo_display(job)
        branch_label = job.get("branch_name") or "—"
        detail_subtitle.set_text(f"{repo_label} · {branch_label}")
        state_norm = _normalize_state(job.get("state"))
        stalled_flag = bool(job.get("stalled"))
        badge_text, badge_classes = _format_state_badge(state_norm, stalled_flag)
        detail_state_badge.set_text(badge_text)
        detail_state_badge.set_classes(
            f"px-3 py-1 text-sm font-semibold rounded-full {badge_classes}"
        )
        if detail_stalled_label is not None:
            if stalled_flag:
                detail_stalled_label.set_text("Stalled")
                detail_stalled_label.set_classes(
                    "text-sm font-semibold text-orange-600"
                )
            else:
                detail_stalled_label.set_text("Heartbeat OK")
                detail_stalled_label.set_classes(
                    "text-sm font-semibold text-emerald-600"
                )
        age_seconds = job.get("age_seconds")
        if detail_age_label is not None:
            if isinstance(age_seconds, (int, float)) and age_seconds >= 0:
                detail_age_label.set_text(f"Age: {int(age_seconds)}s")
            else:
                detail_age_label.set_text("Age: —")
        for field, label in detail_timestamp_labels.items():
            label_text = _format_timestamp_label(
                field.replace("_", " ").title(), job.get(field)
            )
            if label_text:
                label.set_text(label_text)
                label.visible = True
            else:
                label.visible = False
        if detail_metadata_table is not None:
            rows: List[Dict[str, str]] = []
            for key in sorted(job.keys()):
                value = job[key]
                rows.append(
                    {
                        "field": key,
                        "value": json.dumps(value, ensure_ascii=False, default=str),
                    }
                )
            detail_metadata_table.rows = rows

    async def _show_job_detail(job_id: str) -> None:
        if detail_dialog is None:
            return
        if detail_error_label is not None:
            detail_error_label.visible = False
        if detail_loading_label is not None:
            detail_loading_label.visible = True
        detail_dialog.open()
        try:
            job = await client.get_job_detail(job_id)
        except Exception as exc:  # noqa: BLE001
            if detail_error_label is not None:
                detail_error_label.set_text(f"Failed to load job detail: {exc}")
                detail_error_label.visible = True
        else:
            _render_job_detail(job)
        finally:
            if detail_loading_label is not None:
                detail_loading_label.visible = False

    async def _handle_job_selection(event: Any) -> None:
        if not hasattr(event, "selection") or not event.selection:
            return
        row = event.selection[-1]
        job_id = row.get("job_id")
        if not job_id:
            return
        await _show_job_detail(job_id)
        if jobs_table is not None:
            jobs_table.selected.clear()

    detail_dialog = ui.dialog()
    with detail_dialog:
        with ui.card().classes("w-full max-w-4xl p-4"):
            detail_title = ui.label("Job").classes("text-lg font-semibold")
            detail_subtitle = ui.label("").classes("text-sm text-gray-500")
            with ui.row().classes("items-center gap-3 mt-1"):
                detail_state_badge = ui.label("State")
                detail_stalled_label = ui.label("")
            detail_age_label = ui.label("").classes("text-sm text-gray-500")
            detail_error_label = (
                ui.label("").classes("text-sm text-red-600").visible(False)
            )
            detail_loading_label = (
                ui.label("Loading job detail...")
                .classes("text-sm text-gray-500")
                .visible(False)
            )
            for field in TIMESTAMP_FIELDS:
                detail_timestamp_labels[field] = ui.label("").classes(
                    "text-sm text-gray-600"
                )
            with ui.row().classes("gap-2 mt-4"):
                ui.button("Logs (coming later)", disabled=True)
                ui.button("Abort (coming later)", disabled=True)
                ui.button("Close", on_click=detail_dialog.close).props("flat")
            detail_metadata_table = ui.table(
                rows=[],
                columns=[
                    {
                        "name": "field",
                        "label": "Field",
                        "field": "field",
                        "sortable": False,
                    },
                    {
                        "name": "value",
                        "label": "Value",
                        "field": "value",
                        "sortable": False,
                    },
                ],
                row_key="field",
                pagination=0,
            )

    with ui.card().classes("w-full"):
        with ui.row().classes("items-center justify-between gap-4"):
            ui.label("Jobs").classes("text-lg font-semibold")
            with ui.row().classes("items-center gap-2"):
                refresh_button = ui.button("Refresh", on_click=_refresh_jobs)
                sort_button = ui.button("Created ↓", on_click=_toggle_sort)
        ui.label(f"API base: {settings.api_base_url}").classes("text-sm text-gray-500")
        jobs_error_label = ui.label("").classes("text-sm text-red-600").visible(False)
        jobs_loading_label = (
            ui.label("Loading jobs...").classes("text-sm text-gray-500").visible(False)
        )
        jobs_table = ui.table(
            rows=[],
            columns=[
                {
                    "name": "job_id",
                    "label": "Job ID",
                    "field": "job_id",
                    "sortable": False,
                },
                {"name": "repo", "label": "Repo", "field": "repo", "sortable": False},
                {
                    "name": "branch",
                    "label": "Branch",
                    "field": "branch",
                    "sortable": False,
                },
                {
                    "name": "state",
                    "label": "State",
                    "field": "state",
                    "sortable": False,
                },
                {"name": "time", "label": "Time", "field": "time", "sortable": False},
                {
                    "name": "heartbeat",
                    "label": "Heartbeat / Stalled",
                    "field": "heartbeat",
                    "sortable": False,
                },
                {
                    "name": "exit_code",
                    "label": "Exit Code",
                    "field": "exit_code",
                    "sortable": False,
                },
            ],
            row_key="job_id",
            pagination=None,
            selection="single",
        )
        jobs_table.on_select(_handle_job_selection)
        jobs_empty_label = (
            ui.label("No jobs yet. Check back later.")
            .classes("text-sm text-gray-500")
            .visible(False)
        )
    ui.timer(10, _refresh_jobs, on_start=True)


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
            _build_dashboard_panel(settings, client)
        with ui.tab_panel("Submit"):
            _build_submit_panel()
        with ui.tab_panel("Services"):
            _build_services_panel()
