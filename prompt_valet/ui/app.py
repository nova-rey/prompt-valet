"""NiceGUI UI layout for the Prompt Valet control plane."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from nicegui import ui
from nicegui.events import MultiUploadEventArguments

from prompt_valet.ui.client import PromptValetAPIClient, UploadFilePayload
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
    log_textarea: Optional[Any] = None
    log_refresh_button: Optional[Any] = None
    log_loading_label: Optional[Any] = None
    log_error_label: Optional[Any] = None
    sse_status_label: Optional[Any] = None
    live_button: Optional[Any] = None
    pause_button: Optional[Any] = None
    abort_button: Optional[Any] = None
    abort_status_label: Optional[Any] = None
    log_lines: deque[str] = deque(maxlen=600)
    current_job_id: str | None = None
    current_job_state_lower: str | None = None
    live_stream_task: Optional[asyncio.Task] = None
    live_stream_stop: Optional[asyncio.Event] = None
    live_requested = False
    log_refresh_in_progress = False
    abort_in_progress = False

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

    def _render_log_buffer() -> None:
        if log_textarea is None:
            return
        log_textarea.set_value("\n".join(log_lines))

    def _set_logs_from_text(text: str) -> None:
        log_lines.clear()
        log_lines.extend(text.splitlines())
        _render_log_buffer()

    def _append_log_line(line: str) -> None:
        log_lines.append(line)
        _render_log_buffer()

    def _set_sse_status(text: str) -> None:
        if sse_status_label is not None:
            sse_status_label.set_text(text)

    def _update_abort_button_state(state: str | None) -> None:
        if abort_button is None:
            return
        abort_button.disabled = state != "running"

    def _stop_live_stream(message: str | None = None) -> None:
        nonlocal live_requested, live_stream_task, live_stream_stop
        live_requested = False
        if live_stream_stop is not None:
            live_stream_stop.set()
        if live_stream_task is not None:
            live_stream_task.cancel()
        live_stream_task = None
        live_stream_stop = None
        if live_button is not None:
            live_button.set_text("Live logs")
        if pause_button is not None:
            pause_button.disabled = True
        _set_sse_status(message or "Live logs inactive")

    def _start_live_stream(job_id: str) -> None:
        nonlocal live_requested, live_stream_task, live_stream_stop
        if live_requested or not job_id:
            return
        live_requested = True
        if pause_button is not None:
            pause_button.disabled = False
        if live_button is not None:
            live_button.set_text("Live logs (stop)")
        _set_sse_status("Connecting to live logs…")
        stop_event = asyncio.Event()
        live_stream_stop = stop_event
        live_stream_task = asyncio.create_task(
            _run_live_stream(job_id, stop_event),
        )

    def _handle_live_button(_: Any) -> None:
        if live_requested:
            _stop_live_stream("Live logs paused")
            return
        if not current_job_id:
            _set_sse_status("Select a job to stream logs.")
            return
        _start_live_stream(current_job_id)

    async def _run_live_stream(job_id: str, stop_event: asyncio.Event) -> None:
        nonlocal live_requested, live_stream_task, live_stream_stop
        backoff = 0.5
        final_status = "Live logs inactive"
        try:
            while live_requested and not stop_event.is_set():
                try:
                    async for line in client.stream_job_log(job_id):
                        if stop_event.is_set():
                            final_status = "Live logs paused"
                            return
                        _append_log_line(line)
                        _set_sse_status("Live stream active")
                    final_status = "Live stream ended (job terminal)"
                    return
                except asyncio.CancelledError:
                    final_status = "Live logs paused"
                    return
                except Exception as exc:  # noqa: BLE001
                    final_status = f"Reconnecting live logs in {backoff:.1f}s ({exc})"
                    _set_sse_status(final_status)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 10.0)
            final_status = "Live logs inactive"
        finally:
            live_requested = False
            live_stream_task = None
            live_stream_stop = None
            if live_button is not None:
                live_button.set_text("Live logs")
            if pause_button is not None:
                pause_button.disabled = True
            _set_sse_status(final_status)

    async def _load_recent_logs(job_id: str) -> None:
        nonlocal log_refresh_in_progress
        if not job_id or log_refresh_in_progress:
            return
        log_refresh_in_progress = True
        if log_refresh_button is not None:
            log_refresh_button.disabled = True
        if log_loading_label is not None:
            log_loading_label.visible = True
        if log_error_label is not None:
            log_error_label.visible = False
        try:
            text = await client.tail_job_log(job_id)
        except Exception as exc:  # noqa: BLE001
            if log_error_label is not None:
                log_error_label.set_text(f"Failed to load logs: {exc}")
                log_error_label.visible = True
        else:
            _set_logs_from_text(text)
        finally:
            log_refresh_in_progress = False
            if log_refresh_button is not None:
                log_refresh_button.disabled = False
            if log_loading_label is not None:
                log_loading_label.visible = False

    async def _refresh_current_job_detail() -> None:
        if not current_job_id:
            return
        try:
            job = await client.get_job_detail(current_job_id)
        except Exception as exc:  # noqa: BLE001
            if detail_error_label is not None:
                detail_error_label.set_text(f"Failed to refresh job detail: {exc}")
                detail_error_label.visible = True
        else:
            _render_job_detail(job)

    async def _execute_abort(job_id: str) -> None:
        nonlocal abort_in_progress
        if abort_in_progress:
            return
        abort_in_progress = True
        if abort_button is not None:
            abort_button.disabled = True
        try:
            payload = await client.abort_job(job_id)
        except Exception as exc:  # noqa: BLE001
            if abort_status_label is not None:
                abort_status_label.set_text(f"Abort failed: {exc}")
                abort_status_label.set_classes("text-sm text-red-600")
                abort_status_label.visible = True
        else:
            if abort_status_label is not None:
                abort_status_label.set_text(
                    f"Abort requested at {payload.get('abort_requested_at', 'unknown')}"
                )
                abort_status_label.set_classes("text-sm text-amber-600")
                abort_status_label.visible = True
            await _refresh_current_job_detail()
        finally:
            abort_in_progress = False
            _update_abort_button_state(current_job_state_lower)

    def _show_abort_confirmation(_: Any) -> None:
        if not current_job_id:
            return
        selected_job_id = current_job_id
        confirm_dialog = ui.dialog()
        with confirm_dialog:
            ui.label(f"Abort job {selected_job_id}?").classes("font-semibold")
            ui.label("Type ABORT to confirm.").classes("text-sm text-gray-600")
            confirmation_input = ui.input(label="Confirmation text").props(
                "placeholder=ABORT"
            )
            confirmation_error = (
                ui.label("").classes("text-sm text-red-600").visible(False)
            )
            with ui.row().classes("gap-2 mt-2"):
                confirm_button = ui.button("Confirm abort").props("color=negative")
                ui.button("Cancel", on_click=confirm_dialog.close).props("flat")

            def _on_confirm(_: Any) -> None:
                if (confirmation_input.value or "").strip() != "ABORT":
                    confirmation_error.set_text("Please type ABORT to confirm.")
                    confirmation_error.visible = True
                    return
                confirmation_error.visible = False
                confirm_dialog.close()
                asyncio.create_task(_execute_abort(selected_job_id))

            confirm_button.on("click", _on_confirm)
        confirm_dialog.open()

    def _prepare_for_job(job_id: str) -> None:
        nonlocal current_job_id, log_lines, current_job_state_lower
        _stop_live_stream()
        current_job_id = job_id
        current_job_state_lower = None
        log_lines.clear()
        _render_log_buffer()
        if log_error_label is not None:
            log_error_label.visible = False
        if abort_status_label is not None:
            abort_status_label.visible = False
        if log_refresh_button is not None:
            log_refresh_button.disabled = False
        _set_sse_status("Live logs inactive")

    def _render_job_detail(job: Dict[str, Any]) -> None:
        nonlocal current_job_state_lower
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
        current_job_state_lower = state_norm
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
        _update_abort_button_state(current_job_state_lower)

    async def _show_job_detail(job_id: str) -> None:
        if detail_dialog is None:
            return
        _prepare_for_job(job_id)
        _update_abort_button_state(None)
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
            asyncio.create_task(_load_recent_logs(job_id))
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

    def _handle_detail_close() -> None:
        _stop_live_stream("Live logs paused")
        if detail_dialog is not None:
            detail_dialog.close()

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
            with ui.card().classes("mt-4 bg-slate-50 p-3"):
                ui.label("Recent Logs").classes("text-sm font-semibold")
                log_loading_label = (
                    ui.label("Loading logs...")
                    .classes("text-sm text-gray-500")
                    .visible(False)
                )
                log_error_label = (
                    ui.label("").classes("text-sm text-red-600").visible(False)
                )
                log_textarea = (
                    ui.textarea("")
                    .props("readonly")
                    .classes(
                        "w-full min-h-[220px] text-xs sm:text-sm font-mono bg-white"
                    )
                )
                with ui.row().classes("items-center gap-2 mt-3 flex-wrap"):
                    log_refresh_button = ui.button("Refresh logs")
                    log_refresh_button.on(
                        "click",
                        lambda _: asyncio.create_task(
                            _load_recent_logs(current_job_id or "")
                        ),
                    )
                    live_button = ui.button("Live logs", on_click=_handle_live_button)
                    pause_button = ui.button(
                        "Pause/Disconnect",
                        on_click=lambda _: _stop_live_stream("Live logs paused"),
                    )
                    pause_button.disabled = True
                    sse_status_label = (
                        ui.label("Live logs inactive")
                        .classes("text-sm text-gray-500")
                        .style("white-space: nowrap;")
                    )
            with ui.row().classes("items-center gap-2 mt-4 flex-wrap"):
                abort_button = ui.button(
                    "Abort job", on_click=_show_abort_confirmation
                ).props("color=negative")
                ui.button("Close", on_click=_handle_detail_close).props("flat")
            abort_status_label = (
                ui.label("").classes("text-sm text-orange-600 mt-1").visible(False)
            )
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

    detail_dialog.on("close", lambda _: _stop_live_stream("Live logs paused"))
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


def _build_submit_panel(
    settings: UISettings,
    client: PromptValetAPIClient,
    register_connectivity_listener: Callable[[Callable[[bool], None]], None],
) -> None:
    targets_by_repo: Dict[str, List[str]] = {}
    selected_repo: str | None = None
    selected_branch: str | None = None
    selected_uploads: List[UploadFilePayload] = []
    api_reachable = False

    with ui.card().classes("w-full"):
        ui.label("Target selection").classes("text-lg font-semibold")
        ui.label(
            "Pick an inbox repo and branch before submitting prompts or uploads."
        ).classes("text-sm text-gray-500")
        with ui.row().classes("items-end gap-4 flex-wrap mt-3"):
            repo_select = ui.select(
                options=[],
                label="Repo",
                placeholder="Select repository",
                disabled=True,
            ).classes("w-full sm:w-1/3")
            branch_select = ui.select(
                options=[],
                label="Branch",
                placeholder="Select branch",
                disabled=True,
            ).classes("w-full sm:w-1/3")
            target_refresh_button = ui.button("Reload targets")
        target_status_label = ui.label("Loading inbox targets...").classes(
            "text-sm text-gray-500 mt-2"
        )
        target_error_label = ui.label("").classes("text-sm text-red-600")
        target_error_label.visible = False

    with ui.card().classes("mt-4 w-full lg:w-5/6"):
        ui.label("Compose mode").classes("text-lg font-semibold")
        ui.label(
            "Write Markdown, optionally name the file, and let the API drop it into the inbox."
        ).classes("text-sm text-gray-500 mb-2")
        compose_textarea = ui.textarea(
            placeholder="Enter your prompt in Markdown...",
            value="",
            label="Markdown prompt",
        ).classes("w-full")
        filename_input = ui.input(
            label="Optional filename (e.g. greet.prompt.md)",
            placeholder="Leave empty to let the API generate a name",
        ).classes("w-full mt-2")
        compose_error_label = ui.label("").classes("text-sm text-red-600 mt-2")
        compose_error_label.visible = False
        with ui.row().classes("items-center gap-2 mt-2") as compose_result_row:
            compose_result_label = ui.label("")
            compose_result_link = ui.link("View job detail", "#", new_tab=True)
        compose_result_row.visible = False
        compose_result_link.visible = False
        compose_submit_button = ui.button("Submit text")
        compose_submit_button.props("color=primary")
        compose_submit_button.disabled = True

    with ui.card().classes("mt-4 w-full lg:w-5/6"):
        ui.label("Upload mode").classes("text-lg font-semibold")
        ui.label(
            "Upload one or more `.md` files; each is forwarded unchanged to `/api/v1/jobs/upload`."
        ).classes("text-sm text-gray-500 mb-2")
        upload_control = ui.upload(multiple=True, auto_upload=False)
        upload_control.props["accept"] = ".md"
        selected_files_label = ui.label("No files selected").classes(
            "text-sm text-gray-500 mt-2"
        )
        upload_error_label = ui.label("").classes("text-sm text-red-600 mt-2")
        upload_error_label.visible = False
        upload_results_markdown = ui.markdown("")
        upload_results_markdown.classes("text-sm text-gray-600 mt-2")
        upload_results_markdown.visible = False
        upload_submit_button = ui.button("Submit files")
        upload_submit_button.props("color=secondary")
        upload_submit_button.disabled = True

    def _update_compose_button_enabled() -> None:
        ready_text = bool(compose_textarea.value and compose_textarea.value.strip())
        ready = bool(api_reachable and selected_repo and selected_branch and ready_text)
        compose_submit_button.disabled = not ready

    def _update_selected_files_label() -> None:
        count = len(selected_uploads)
        if count:
            selected_files_label.set_text(f"{count} file(s) selected")
        else:
            selected_files_label.set_text("No files selected")

    def _update_upload_button_enabled() -> None:
        ready = bool(
            api_reachable and selected_repo and selected_branch and selected_uploads
        )
        upload_submit_button.disabled = not ready

    async def _refresh_targets() -> None:
        nonlocal selected_repo, selected_branch, targets_by_repo
        target_status_label.set_text("Loading inbox targets...")
        target_error_label.visible = False
        repo_select.disabled = True
        branch_select.disabled = True
        try:
            discovered = await client.list_targets()
        except Exception as exc:  # noqa: BLE001
            targets_by_repo.clear()
            repo_select.options = []
            branch_select.options = []
            selected_repo = None
            selected_branch = None
            target_status_label.set_text("Unable to load targets")
            target_error_label.set_text(f"Failed to load targets: {exc}")
            target_error_label.visible = True
            _update_compose_button_enabled()
            _update_upload_button_enabled()
            return

        if not discovered:
            targets_by_repo.clear()
            repo_select.options = []
            branch_select.options = []
            selected_repo = None
            selected_branch = None
            branch_select.disabled = True
            repo_select.disabled = True
            target_status_label.set_text("No inbox targets discovered.")
            target_error_label.visible = False
            _update_compose_button_enabled()
            _update_upload_button_enabled()
            return

        new_map: Dict[str, List[str]] = {}
        for target in discovered:
            display_repo = target.get("full_repo") or target.get("repo") or ""
            branch = target.get("branch")
            if not display_repo or not branch:
                continue
            new_map.setdefault(display_repo, []).append(branch)

        for repo_key, branches in new_map.items():
            new_map[repo_key] = sorted(set(branches))

        targets_by_repo.clear()
        targets_by_repo.update(new_map)
        repo_options = sorted(targets_by_repo.keys())
        repo_select.options = repo_options
        repo_select.disabled = not bool(repo_options)
        if repo_options:
            selected_repo = repo_options[0]
            repo_select.value = selected_repo
            branch_options = targets_by_repo[selected_repo]
            branch_select.options = branch_options
            branch_select.disabled = not bool(branch_options)
            if branch_options:
                selected_branch = branch_options[0]
                branch_select.value = selected_branch
            else:
                selected_branch = None
                branch_select.value = None
        else:
            selected_repo = None
            selected_branch = None
            branch_select.options = []
            branch_select.value = None
            branch_select.disabled = True

        target_status_label.set_text(f"{len(repo_options)} inbox repo(s) available")
        _update_compose_button_enabled()
        _update_upload_button_enabled()

    def _on_repo_change(_: Any) -> None:
        nonlocal selected_repo, selected_branch
        repo_value = repo_select.value or None
        selected_repo = repo_value
        if repo_value:
            branches = targets_by_repo.get(repo_value, [])
            branch_select.options = branches
            branch_select.disabled = not bool(branches)
            if branches:
                selected_branch = branches[0]
                branch_select.value = selected_branch
            else:
                selected_branch = None
                branch_select.value = None
        else:
            branch_select.options = []
            branch_select.value = None
            branch_select.disabled = True
            selected_branch = None
        _update_compose_button_enabled()
        _update_upload_button_enabled()

    def _on_branch_change(_: Any) -> None:
        nonlocal selected_branch
        selected_branch = branch_select.value or None
        _update_compose_button_enabled()
        _update_upload_button_enabled()

    async def _handle_file_selection(event: MultiUploadEventArguments) -> None:
        nonlocal selected_uploads
        selected_uploads.clear()
        upload_error_label.visible = False
        for file in event.files:
            name = file.name
            if not name or not name.lower().endswith(".md"):
                upload_error_label.set_text("Only '.md' files are accepted.")
                upload_error_label.visible = True
                continue
            try:
                data = await file.read()
            except Exception as exc:  # noqa: BLE001
                upload_error_label.set_text(f"Failed to read {name}: {exc}")
                upload_error_label.visible = True
                continue
            selected_uploads.append(
                UploadFilePayload(
                    filename=name,
                    data=data,
                    content_type=getattr(file, "content_type", None),
                )
            )
        _update_selected_files_label()
        _update_upload_button_enabled()

    async def _submit_composed() -> None:
        compose_error_label.visible = False
        compose_result_row.visible = False
        compose_submit_button.disabled = True
        if not selected_repo or not selected_branch:
            _update_compose_button_enabled()
            return
        filename_value = (
            filename_input.value.strip()
            if filename_input.value and filename_input.value.strip()
            else None
        )
        try:
            payload = await client.submit_job(
                selected_repo or "",
                selected_branch or "",
                compose_textarea.value or "",
                filename_value,
            )
        except Exception as exc:  # noqa: BLE001
            compose_error_label.set_text(f"Submission failed: {exc}")
            compose_error_label.visible = True
        else:
            job_id = payload.get("job_id") or ""
            compose_result_label.set_text(f"Job ID: {job_id}")
            if job_id:
                compose_result_link.props["href"] = (
                    f"{settings.api_base_url}/jobs/{job_id}"
                )
                compose_result_link.visible = True
            else:
                compose_result_link.visible = False
            compose_result_row.visible = True
        finally:
            _update_compose_button_enabled()

    async def _submit_upload() -> None:
        upload_error_label.visible = False
        upload_results_markdown.visible = False
        upload_submit_button.disabled = True
        if not selected_repo or not selected_branch or not selected_uploads:
            _update_upload_button_enabled()
            return
        try:
            jobs = await client.upload_jobs(
                selected_repo or "",
                selected_branch or "",
                selected_uploads,
            )
        except Exception as exc:  # noqa: BLE001
            upload_error_label.set_text(f"Upload failed: {exc}")
            upload_error_label.visible = True
        else:
            if jobs:
                lines = [
                    "| Filename | Job ID | Details |",
                    "| --- | --- | --- |",
                ]
                for payload, job in zip(selected_uploads, jobs):
                    job_id = job.get("job_id") or "unknown"
                    view_url = f"{settings.api_base_url}/jobs/{job_id}"
                    lines.append(
                        f"| {payload.filename} | {job_id} | [View job]({view_url}) |"
                    )
                upload_results_markdown.set_text(
                    "### Upload results\n" + "\n".join(lines)
                )
                upload_results_markdown.visible = True
            else:
                upload_results_markdown.set_text(
                    "Upload succeeded with no jobs returned."
                )
                upload_results_markdown.visible = True
        finally:
            selected_uploads.clear()
            upload_control.reset()
            _update_selected_files_label()
            _update_upload_button_enabled()

    compose_textarea.on_change(lambda _: _update_compose_button_enabled())
    repo_select.on_change(_on_repo_change)
    branch_select.on_change(_on_branch_change)
    upload_control.on_multi_upload(_handle_file_selection)
    compose_submit_button.on("click", lambda _: asyncio.create_task(_submit_composed()))
    upload_submit_button.on("click", lambda _: asyncio.create_task(_submit_upload()))
    target_refresh_button.on("click", lambda _: asyncio.create_task(_refresh_targets()))

    def _set_connectivity(reachable: bool) -> None:
        nonlocal api_reachable
        api_reachable = reachable
        _update_compose_button_enabled()
        _update_upload_button_enabled()

    register_connectivity_listener(_set_connectivity)
    asyncio.create_task(_refresh_targets())


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
    connectivity_listeners: List[Callable[[bool], None]] = []
    api_connectivity_reachable = False

    def register_connectivity_listener(listener: Callable[[bool], None]) -> None:
        connectivity_listeners.append(listener)
        listener(api_connectivity_reachable)

    with ui.header().classes("justify-between px-6"):
        ui.label("Prompt Valet UI").classes("text-lg font-semibold")
        with ui.row().classes("items-center gap-3"):
            status_icon = ui.icon("cloud").classes("text-xl text-gray-400")
            status_label = ui.label("Checking API...").classes(
                "font-medium text-gray-500"
            )

    async def refresh_connectivity() -> None:
        nonlocal api_connectivity_reachable
        report = await client.ping()
        api_connectivity_reachable = report.reachable
        for listener in connectivity_listeners:
            listener(api_connectivity_reachable)
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
            _build_submit_panel(settings, client, register_connectivity_listener)
        with ui.tab_panel("Services"):
            _build_services_panel()
