"""NiceGUI UI layout for the Prompt Valet control plane."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional

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
_SERVICE_TARGET_PREVIEW = 4


def _schedule_async(factory: Callable[[], Coroutine[Any, Any, Any]]) -> None:
    """Enqueue a coroutine via a one-shot NiceGUI timer so the event loop is active."""
    ui.timer(0, lambda: asyncio.create_task(factory()), once=True)


logger = logging.getLogger(__name__)
_PV_UI_DEBUG_REFRESH = bool(os.getenv("PV_UI_DEBUG_REFRESH"))
_LAST_TEXT_VALUES: Dict[str, str] = {}


def _should_update(key: str, value: Any) -> bool:
    normalized = "" if value is None else str(value)
    previous = _LAST_TEXT_VALUES.get(key)
    if previous == normalized:
        return False
    _LAST_TEXT_VALUES[key] = normalized
    return True


def _set_text_if_changed(el: Any, value: str) -> None:
    """Set element text only when it changes to avoid UI flicker from timer refresh loops."""

    def _normalize_key(element: Any) -> str:
        return getattr(
            element,
            "_pv_text_key",
            f"{type(element).__name__}@{id(element)}:text",
        )

    key = _normalize_key(el)
    previous = _LAST_TEXT_VALUES.get(key)
    if not _should_update(key, value):
        return
    if _PV_UI_DEBUG_REFRESH:
        logger.debug(
            "UI text update on %s: %r -> %r",
            type(el).__name__,
            previous,
            value,
        )
    try:
        setattr(el, "_pv_last_text", value)
    except Exception:  # pragma: no cover - defensive
        pass

    if hasattr(el, "set_text"):
        el.set_text(value)
    elif hasattr(el, "text"):
        el.text = value
    elif hasattr(el, "content"):
        el.content = value


def _set_visibility_if_changed(el: Any, visible: bool) -> None:
    current: Any = None
    try:
        current = getattr(el, "_pv_last_visible", None)
        if current == visible:
            return
        setattr(el, "_pv_last_visible", visible)
    except Exception:  # pragma: no cover - defensive
        pass
    if _PV_UI_DEBUG_REFRESH:
        logger.debug(
            "UI visibility update on %s: %r -> %r",
            type(el).__name__,
            current,
            visible,
        )

    if hasattr(el, "set_visibility"):
        el.set_visibility(visible)
    else:
        setattr(el, "visible", visible)


def _set_classes_if_changed(el: Any, classes: str) -> None:
    current: Any = None
    try:
        current = getattr(el, "_pv_last_classes", None)
        if current == classes:
            return
        setattr(el, "_pv_last_classes", classes)
    except Exception:  # pragma: no cover - defensive
        pass
    if _PV_UI_DEBUG_REFRESH:
        logger.debug(
            "UI class update on %s: %r -> %r",
            type(el).__name__,
            current,
            classes,
        )

    el.classes(classes)


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
    detail_dialog_open = False
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
            _set_visibility_if_changed(jobs_empty_label, not bool(rows))

    async def _refresh_jobs() -> None:
        nonlocal refresh_in_progress, jobs_data
        if refresh_in_progress:
            return
        refresh_in_progress = True
        if refresh_button is not None:
            refresh_button.disabled = True
        if jobs_loading_label is not None:
            _set_visibility_if_changed(jobs_loading_label, True)
        try:
            jobs_data = await client.list_jobs()
            if jobs_error_label is not None:
                _set_visibility_if_changed(jobs_error_label, False)
            _update_jobs_table()
        except Exception as exc:  # noqa: BLE001
            if jobs_error_label is not None:
                _set_text_if_changed(jobs_error_label, f"Failed to load jobs: {exc}")
                _set_visibility_if_changed(jobs_error_label, True)
        finally:
            refresh_in_progress = False
            if refresh_button is not None:
                refresh_button.disabled = False
            if jobs_loading_label is not None:
                _set_visibility_if_changed(jobs_loading_label, False)

    def _toggle_sort() -> None:
        nonlocal sort_descending
        sort_descending = not sort_descending
        if sort_button is not None:
            label = "Created ↓" if sort_descending else "Created ↑"
            _set_text_if_changed(sort_button, label)
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

    def _copy_job_id(_: Any) -> None:
        job_id = current_job_id
        if not job_id:
            ui.notify("Select a job before copying the ID.", color="warning")
            return
        ui.run_javascript(f"navigator.clipboard.writeText({json.dumps(job_id)})")
        ui.notify("Job ID copied to clipboard.", color="positive")

    def _set_sse_status(text: str) -> None:
        if sse_status_label is not None:
            _set_text_if_changed(sse_status_label, text)

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
            _set_text_if_changed(live_button, "Live logs")
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
            _set_text_if_changed(live_button, "Live logs (stop)")
        _set_sse_status("Connecting to live logs…")
        stop_event = asyncio.Event()
        live_stream_stop = stop_event

        def _launch_stream() -> None:
            nonlocal live_stream_task
            live_stream_task = asyncio.create_task(
                _run_live_stream(job_id, stop_event),
            )

        ui.timer(0, _launch_stream, once=True)

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
                _set_text_if_changed(live_button, "Live logs")
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
            _set_visibility_if_changed(log_loading_label, True)
        if log_error_label is not None:
            _set_visibility_if_changed(log_error_label, False)
        try:
            text = await client.tail_job_log(job_id)
        except Exception as exc:  # noqa: BLE001
            if log_error_label is not None:
                _set_text_if_changed(log_error_label, f"Failed to load logs: {exc}")
                _set_visibility_if_changed(log_error_label, True)
        else:
            _set_logs_from_text(text)
        finally:
            log_refresh_in_progress = False
            if log_refresh_button is not None:
                log_refresh_button.disabled = False
            if log_loading_label is not None:
                _set_visibility_if_changed(log_loading_label, False)

    async def _refresh_current_job_detail() -> None:
        if not current_job_id:
            return
        try:
            job = await client.get_job_detail(current_job_id)
        except Exception as exc:  # noqa: BLE001
            if detail_error_label is not None:
                _set_text_if_changed(
                    detail_error_label, f"Failed to refresh job detail: {exc}"
                )
                _set_visibility_if_changed(detail_error_label, True)
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
                _set_text_if_changed(abort_status_label, f"Abort failed: {exc}")
                _set_classes_if_changed(abort_status_label, "text-sm text-red-600")
                _set_visibility_if_changed(abort_status_label, True)
        else:
            if abort_status_label is not None:
                _set_text_if_changed(
                    abort_status_label,
                    f"Abort requested at {payload.get('abort_requested_at', 'unknown')}",
                )
                _set_classes_if_changed(abort_status_label, "text-sm text-amber-600")
                _set_visibility_if_changed(abort_status_label, True)
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
            confirmation_error = ui.label("").classes("text-sm text-red-600")
            _set_visibility_if_changed(confirmation_error, False)
            with ui.row().classes("gap-2 mt-2"):
                confirm_button = ui.button("Confirm abort").props("color=negative")
                ui.button("Cancel", on_click=confirm_dialog.close).props("flat")

            def _on_confirm(_: Any) -> None:
                if (confirmation_input.value or "").strip() != "ABORT":
                    _set_text_if_changed(
                        confirmation_error, "Please type ABORT to confirm."
                    )
                    _set_visibility_if_changed(confirmation_error, True)
                    return
                _set_visibility_if_changed(confirmation_error, False)
                confirm_dialog.close()
                _schedule_async(lambda: _execute_abort(selected_job_id))

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
            _set_visibility_if_changed(log_error_label, False)
        if abort_status_label is not None:
            _set_visibility_if_changed(abort_status_label, False)
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
        _set_text_if_changed(detail_title, f"Job {job_id}")
        repo_label = _repo_display(job)
        branch_label = job.get("branch_name") or "—"
        _set_text_if_changed(detail_subtitle, f"{repo_label} · {branch_label}")
        state_norm = _normalize_state(job.get("state"))
        stalled_flag = bool(job.get("stalled"))
        current_job_state_lower = state_norm
        badge_text, badge_classes = _format_state_badge(state_norm, stalled_flag)
        _set_text_if_changed(detail_state_badge, badge_text)
        _set_classes_if_changed(
            detail_state_badge,
            f"px-3 py-1 text-sm font-semibold rounded-full {badge_classes}",
        )
        if detail_stalled_label is not None:
            if stalled_flag:
                _set_text_if_changed(detail_stalled_label, "Stalled")
                _set_classes_if_changed(
                    detail_stalled_label, "text-sm font-semibold text-orange-600"
                )
            else:
                _set_text_if_changed(detail_stalled_label, "Heartbeat OK")
                _set_classes_if_changed(
                    detail_stalled_label, "text-sm font-semibold text-emerald-600"
                )
        age_seconds = job.get("age_seconds")
        if detail_age_label is not None:
            if isinstance(age_seconds, (int, float)) and age_seconds >= 0:
                _set_text_if_changed(detail_age_label, f"Age: {int(age_seconds)}s")
            else:
                _set_text_if_changed(detail_age_label, "Age: —")
        for field, label in detail_timestamp_labels.items():
            label_text = _format_timestamp_label(
                field.replace("_", " ").title(), job.get(field)
            )
            if label_text:
                _set_text_if_changed(label, label_text)
                _set_visibility_if_changed(label, True)
            else:
                _set_visibility_if_changed(label, False)
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
        nonlocal detail_dialog_open
        if detail_dialog is None:
            return
        _prepare_for_job(job_id)
        _update_abort_button_state(None)
        if detail_error_label is not None:
            _set_visibility_if_changed(detail_error_label, False)
        if detail_loading_label is not None:
            _set_visibility_if_changed(detail_loading_label, True)
        detail_dialog_open = True
        detail_dialog.open()
        try:
            job = await client.get_job_detail(job_id)
        except Exception as exc:  # noqa: BLE001
            if detail_error_label is not None:
                _set_text_if_changed(
                    detail_error_label, f"Failed to load job detail: {exc}"
                )
                _set_visibility_if_changed(detail_error_label, True)
        else:
            _render_job_detail(job)
            _schedule_async(lambda: _load_recent_logs(job_id))
        finally:
            if detail_loading_label is not None:
                _set_visibility_if_changed(detail_loading_label, False)

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
        nonlocal detail_dialog_open
        detail_dialog_open = False
        _stop_live_stream("Live logs paused")
        if detail_dialog is not None:
            detail_dialog.close()

    def _handle_detail_dialog_closed(_: Any) -> None:
        nonlocal detail_dialog_open
        detail_dialog_open = False
        _stop_live_stream("Live logs paused")

    detail_dialog = ui.dialog()
    with detail_dialog:
        with (
            ui.card()
            .classes("w-full max-w-4xl p-4")
            .style("max-height: calc(100vh - 120px); overflow-y: auto;")
        ):
            with ui.row().classes("items-center gap-3 flex-wrap"):
                detail_title = ui.label("Job").classes("text-lg font-semibold")
                ui.button("Copy ID", on_click=_copy_job_id).props("flat")
            detail_subtitle = ui.label("").classes("text-sm text-gray-500")
            with ui.row().classes("items-center gap-3 mt-1"):
                detail_state_badge = ui.label("State")
                detail_stalled_label = ui.label("")
            detail_age_label = ui.label("").classes("text-sm text-gray-500")
            detail_error_label = ui.label("").classes("text-sm text-red-600")
            _set_visibility_if_changed(detail_error_label, False)
            detail_loading_label = ui.label("Loading job detail...").classes(
                "text-sm text-gray-500"
            )
            _set_visibility_if_changed(detail_loading_label, False)
            for field in TIMESTAMP_FIELDS:
                detail_timestamp_labels[field] = ui.label("").classes(
                    "text-sm text-gray-600"
                )
            with ui.card().classes("mt-4 bg-slate-50 p-3").style("overflow: hidden;"):
                ui.label("Recent Logs").classes("text-sm font-semibold")
                log_loading_label = ui.label("Loading logs...").classes(
                    "text-sm text-gray-500"
                )
                _set_visibility_if_changed(log_loading_label, False)
                log_error_label = ui.label("").classes("text-sm text-red-600")
                _set_visibility_if_changed(log_error_label, False)
                log_textarea = (
                    ui.textarea("")
                    .props("readonly")
                    .classes(
                        "w-full min-h-[220px] max-h-[260px] text-xs sm:text-sm font-mono bg-white overflow-y-auto"
                    )
                )
                with ui.row().classes(
                    "items-stretch gap-2 mt-3 flex-col sm:flex-row",
                ):
                    log_refresh_button = ui.button("Refresh logs").classes(
                        "w-full sm:w-auto"
                    )
                    log_refresh_button.on(
                        "click",
                        lambda _: _schedule_async(
                            lambda: _load_recent_logs(current_job_id or "")
                        ),
                    )
                    live_button = ui.button(
                        "Live logs", on_click=_handle_live_button
                    ).classes("w-full sm:w-auto")
                    pause_button = ui.button(
                        "Pause/Disconnect",
                        on_click=lambda _: _stop_live_stream("Live logs paused"),
                    ).classes("w-full sm:w-auto")
                    pause_button.disabled = True
                    sse_status_label = ui.label("Live logs inactive").classes(
                        "text-sm text-gray-500 break-words"
                    )
            with ui.row().classes(
                "items-stretch gap-2 mt-4 flex-col sm:flex-row",
            ):
                abort_button = (
                    ui.button("Abort job", on_click=_show_abort_confirmation)
                    .props("color=negative")
                    .classes("w-full sm:w-auto")
                )
                ui.button("Close", on_click=_handle_detail_close).props("flat").classes(
                    "w-full sm:w-auto"
                )
            abort_status_label = ui.label("").classes("text-sm text-orange-600 mt-1")
            _set_visibility_if_changed(abort_status_label, False)
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

    detail_dialog.on("close", _handle_detail_dialog_closed)
    with ui.card().classes("w-full"):
        with ui.row().classes("items-center justify-between gap-4"):
            ui.label("Jobs").classes("text-lg font-semibold")
            with ui.row().classes("items-center gap-2"):
                refresh_button = ui.button("Refresh", on_click=_refresh_jobs)
                sort_button = ui.button("Created ↓", on_click=_toggle_sort)
        ui.label(f"API base: {settings.api_base_url}").classes("text-sm text-gray-500")
        jobs_error_label = ui.label("").classes("text-sm text-red-600")
        _set_visibility_if_changed(jobs_error_label, False)
        jobs_loading_label = ui.label("Loading jobs...").classes(
            "text-sm text-gray-500"
        )
        _set_visibility_if_changed(jobs_loading_label, False)
        with ui.element("div").classes("w-full overflow-x-auto mt-2"):
            jobs_table = ui.table(
                rows=[],
                columns=[
                    {
                        "name": "job_id",
                        "label": "Job ID",
                        "field": "job_id",
                        "sortable": False,
                    },
                    {
                        "name": "repo",
                        "label": "Repo",
                        "field": "repo",
                        "sortable": False,
                    },
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
                    {
                        "name": "time",
                        "label": "Time",
                        "field": "time",
                        "sortable": False,
                    },
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
            ).classes("min-w-[660px]")
        jobs_table.on_select(_handle_job_selection)
        jobs_empty_label = ui.label("No jobs yet. Check back later.").classes(
            "text-sm text-gray-500"
        )
        _set_visibility_if_changed(jobs_empty_label, False)

    def _stop_live_stream_when_hidden() -> None:
        nonlocal detail_dialog_open, live_stream_task
        if detail_dialog_open:
            return
        if live_stream_task is None:
            return
        _stop_live_stream("Live logs paused (dialog hidden)")

    _schedule_async(_refresh_jobs)
    ui.timer(2.0, _refresh_jobs, immediate=False)
    ui.timer(5, _stop_live_stream_when_hidden)


def _build_submit_panel(
    settings: UISettings,
    client: PromptValetAPIClient,
    register_connectivity_listener: Callable[[Callable[[bool], None]], None],
    test_context: Dict[str, Any] | None = None,
) -> None:
    targets_by_repo: Dict[str, List[str]] = {}
    selected_repo: str | None = None
    selected_branch: str | None = None
    selected_uploads: List[UploadFilePayload] = []
    select_events_suppressed = False
    api_reachable = False

    with ui.card().classes("w-full"):
        ui.label("Target selection").classes("text-lg font-semibold")
        ui.label(
            "Pick an inbox repo and branch before submitting prompts or uploads."
        ).classes("text-sm text-gray-500")
        with ui.row().classes("items-end gap-3 mt-3 flex-col sm:flex-row"):
            repo_select = ui.select(
                options=[],
                label="Repo",
            ).classes("w-full sm:w-1/3")
            branch_select = ui.select(
                options=[],
                label="Branch",
            ).classes("w-full sm:w-1/3")
            target_refresh_button = ui.button("Reload targets")
        target_refresh_button.classes("w-full sm:w-auto self-start")
        target_status_label = ui.label("Loading inbox targets...").classes(
            "text-sm text-gray-500 mt-2"
        )
        target_error_label = ui.label("").classes("text-sm text-red-600")
        _set_visibility_if_changed(target_error_label, False)

    with ui.card().classes("mt-4 w-full lg:w-5/6"):
        ui.label("Compose mode").classes("text-lg font-semibold")
        ui.label(
            "Write Markdown, optionally name the file, and let the API drop it into the inbox."
        ).classes("text-sm text-gray-500 mb-2")
        compose_textarea = ui.textarea(
            value="",
            label="Markdown prompt",
        ).classes("w-full")
        filename_input = ui.input(
            label="Optional filename (e.g. greet.prompt.md)",
        ).classes("w-full mt-2")
        compose_error_label = ui.label("").classes("text-sm text-red-600 mt-2")
        _set_visibility_if_changed(compose_error_label, False)
        with ui.row().classes(
            "items-start gap-2 mt-2 flex-col sm:flex-row"
        ) as compose_result_row:
            compose_result_label = ui.label("")
            compose_result_link = ui.link("View job detail", "#", new_tab=True)
            compose_result_link.classes("break-words")
        _set_visibility_if_changed(compose_result_row, False)
        _set_visibility_if_changed(compose_result_link, False)
        compose_submit_button = ui.button("Submit text").classes("w-full sm:w-auto")
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
            "text-sm text-gray-500 mt-2 break-words"
        )
        upload_error_label = ui.label("").classes("text-sm text-red-600 mt-2")
        _set_visibility_if_changed(upload_error_label, False)
        upload_results_markdown = ui.markdown("")
        upload_results_markdown.classes("text-sm text-gray-600 mt-2 break-words")
        _set_visibility_if_changed(upload_results_markdown, False)
        with ui.row().classes("items-stretch gap-2 mt-2 flex-col sm:flex-row"):
            upload_submit_button = ui.button("Submit files").classes("w-full sm:w-auto")
        upload_submit_button.props("color=secondary")
        upload_submit_button.disabled = True

    def _update_compose_button_enabled() -> None:
        ready_text = bool(compose_textarea.value and compose_textarea.value.strip())
        ready = bool(api_reachable and selected_repo and selected_branch and ready_text)
        compose_submit_button.disabled = not ready

    def _update_selected_files_label() -> None:
        count = len(selected_uploads)
        if count:
            _set_text_if_changed(selected_files_label, f"{count} file(s) selected")
        else:
            _set_text_if_changed(selected_files_label, "No files selected")

    def _update_upload_button_enabled() -> None:
        ready = bool(
            api_reachable and selected_repo and selected_branch and selected_uploads
        )
        upload_submit_button.disabled = not ready

    def _execute_with_select_suppressed(action: Callable[[], None]) -> None:
        nonlocal select_events_suppressed
        select_events_suppressed = True
        try:
            action()
        finally:
            select_events_suppressed = False

    def _refresh_repo_options(repo_options: List[str]) -> None:
        nonlocal selected_repo

        def update() -> None:
            nonlocal selected_repo
            repo_select.options = repo_options
            repo_select.disabled = not bool(repo_options)
            if selected_repo not in repo_options:
                selected_repo = repo_options[0] if repo_options else None
            repo_select.value = selected_repo

        _execute_with_select_suppressed(update)

    def _refresh_branch_options_for_repo(repo: str | None) -> None:
        nonlocal selected_branch

        def update() -> None:
            nonlocal selected_branch
            if repo:
                branches = targets_by_repo.get(repo, [])
                branch_select.options = branches
                branch_select.disabled = not bool(branches)
                if selected_branch not in branches:
                    selected_branch = branches[0] if branches else None
            else:
                branch_select.options = []
                branch_select.disabled = True
                selected_branch = None
            branch_select.value = selected_branch

        _execute_with_select_suppressed(update)

    async def _refresh_targets() -> None:
        nonlocal selected_repo, selected_branch, targets_by_repo
        _set_visibility_if_changed(target_error_label, False)
        repo_select.disabled = True
        branch_select.disabled = True
        try:
            discovered = await client.list_targets()
        except Exception as exc:  # noqa: BLE001
            targets_by_repo.clear()
            _refresh_repo_options([])
            _refresh_branch_options_for_repo(selected_repo)
            _set_text_if_changed(target_status_label, "Unable to load targets")
            _set_text_if_changed(target_error_label, f"Failed to load targets: {exc}")
            _set_visibility_if_changed(target_error_label, True)
            _update_compose_button_enabled()
            _update_upload_button_enabled()
            return

        if not discovered:
            targets_by_repo.clear()
            _refresh_repo_options([])
            _refresh_branch_options_for_repo(selected_repo)
            _set_text_if_changed(target_status_label, "No inbox targets discovered.")
            _set_visibility_if_changed(target_error_label, False)
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
        _refresh_repo_options(repo_options)
        _refresh_branch_options_for_repo(selected_repo)

        _set_text_if_changed(
            target_status_label, f"{len(repo_options)} inbox repo(s) available"
        )
        _update_compose_button_enabled()
        _update_upload_button_enabled()

    def _on_repo_change(event: Any) -> None:
        nonlocal selected_repo
        if select_events_suppressed:
            return
        selected_repo = event.value or None
        _refresh_branch_options_for_repo(selected_repo)
        _update_compose_button_enabled()
        _update_upload_button_enabled()

    def _on_branch_change(event: Any) -> None:
        nonlocal selected_branch
        if select_events_suppressed:
            return
        selected_branch = event.value or None
        _update_compose_button_enabled()
        _update_upload_button_enabled()

    async def _handle_file_selection(event: MultiUploadEventArguments) -> None:
        nonlocal selected_uploads
        selected_uploads.clear()
        _set_visibility_if_changed(upload_error_label, False)
        for file in event.files:
            name = file.name
            if not name or not name.lower().endswith(".md"):
                _set_text_if_changed(
                    upload_error_label, "Only '.md' files are accepted."
                )
                _set_visibility_if_changed(upload_error_label, True)
                continue
            try:
                data = await file.read()
            except Exception as exc:  # noqa: BLE001
                _set_text_if_changed(
                    upload_error_label, f"Failed to read {name}: {exc}"
                )
                _set_visibility_if_changed(upload_error_label, True)
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
        _set_visibility_if_changed(compose_error_label, False)
        _set_visibility_if_changed(compose_result_row, False)
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
            _set_text_if_changed(compose_error_label, f"Submission failed: {exc}")
            _set_visibility_if_changed(compose_error_label, True)
        else:
            job_id = payload.get("job_id") or ""
            _set_text_if_changed(compose_result_label, f"Job ID: {job_id}")
            if job_id:
                compose_result_link.props["href"] = (
                    f"{settings.api_base_url}/jobs/{job_id}"
                )
                _set_visibility_if_changed(compose_result_link, True)
            else:
                _set_visibility_if_changed(compose_result_link, False)
            _set_visibility_if_changed(compose_result_row, True)
        finally:
            _update_compose_button_enabled()

    async def _submit_upload() -> None:
        _set_visibility_if_changed(upload_error_label, False)
        _set_visibility_if_changed(upload_results_markdown, False)
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
            _set_text_if_changed(upload_error_label, f"Upload failed: {exc}")
            _set_visibility_if_changed(upload_error_label, True)
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
                _set_text_if_changed(
                    upload_results_markdown,
                    "### Upload results\n" + "\n".join(lines),
                )
                _set_visibility_if_changed(upload_results_markdown, True)
            else:
                _set_text_if_changed(
                    upload_results_markdown,
                    "Upload succeeded with no jobs returned.",
                )
                _set_visibility_if_changed(upload_results_markdown, True)
        finally:
            selected_uploads.clear()
            upload_control.reset()
            _update_selected_files_label()
        _update_upload_button_enabled()

    compose_textarea.on("input", lambda _: _update_compose_button_enabled())
    repo_select.on("change", _on_repo_change)
    branch_select.on("change", _on_branch_change)
    upload_control.on_multi_upload(_handle_file_selection)
    compose_submit_button.on(
        "click",
        lambda _: _schedule_async(lambda: _submit_composed()),
    )
    upload_submit_button.on(
        "click",
        lambda _: _schedule_async(lambda: _submit_upload()),
    )
    target_refresh_button.on("click", lambda _: _schedule_async(_refresh_targets))

    def _set_connectivity(reachable: bool) -> None:
        nonlocal api_reachable
        api_reachable = reachable
        _update_compose_button_enabled()
        _update_upload_button_enabled()

    register_connectivity_listener(_set_connectivity)
    if test_context is not None:
        submit_panel_hooks = test_context.setdefault("submit_panel", {})
        submit_panel_hooks["target_status_label"] = target_status_label
        submit_panel_hooks["refresh_targets"] = _refresh_targets

    _schedule_async(_refresh_targets)
    ui.timer(2.0, _refresh_targets, immediate=False)


def _build_services_panel(
    client: PromptValetAPIClient,
    register_connectivity_listener: Callable[[bool], None],
    test_context: Dict[str, Any] | None = None,
) -> None:
    ui.markdown("### Services overview")
    ui.markdown(
        "Watcher and TreeBuilder visibility is derived from the existing `/status`, `/jobs`, and `/targets` APIs."
    )

    refresh_button = ui.button(
        "Refresh services",
        icon="refresh",
        on_click=lambda _: _schedule_async(_refresh_services),
    ).classes("w-full sm:w-auto")
    refresh_button.disabled = True

    CONNECTIVITY_HINT_BASE_CLASSES = "text-sm break-words whitespace-pre-line"
    connectivity_hint_label = ui.label("Awaiting connectivity...").classes(
        f"{CONNECTIVITY_HINT_BASE_CLASSES} text-gray-500"
    )

    watcher_error_label = ui.label("").classes("text-sm text-rose-600")
    _set_visibility_if_changed(watcher_error_label, False)
    tree_error_label = ui.label("").classes("text-sm text-rose-600")
    _set_visibility_if_changed(tree_error_label, False)

    with ui.row().classes("items-center gap-3 mt-2 flex-col sm:flex-row"):
        refresh_button
        connectivity_hint_label

    watcher_status_badge: Any
    tree_status_badge: Any
    watcher_status_detail: Any
    watcher_heartbeat_label: Any
    watcher_message_label: Any
    watcher_detail_label: Any
    tree_message_label: Any
    tree_detail_label: Any
    tree_target_count_label: Any
    target_list_markdown: Any

    with ui.row().classes("flex flex-wrap gap-4 mt-4"):
        with ui.card().classes("w-full lg:w-1/2 p-4"):
            ui.label("Watcher").classes("text-base font-semibold")
            with ui.row().classes("items-center justify-between mt-2"):
                ui.label("Status").classes("text-sm text-gray-500")
                watcher_status_badge = ui.label("Loading...").classes(
                    "px-3 py-1 text-sm font-semibold rounded-full bg-amber-100 text-amber-700"
                )
            watcher_status_detail = ui.label("Refreshing watcher state...").classes(
                "text-sm text-gray-600 mt-1"
            )
            watcher_heartbeat_label = ui.label("Last heartbeat: —").classes(
                "text-sm text-gray-500 mt-1"
            )
            watcher_message_label = ui.label("Pending watcher data.").classes(
                "text-sm text-gray-600 mt-1"
            )
            watcher_detail_label = ui.label("Runs root: —").classes(
                "text-sm text-gray-500 mt-2"
            )
            watcher_error_label
        with ui.card().classes("w-full lg:w-1/2 p-4"):
            ui.label("TreeBuilder").classes("text-base font-semibold")
            with ui.row().classes("items-center justify-between mt-2"):
                ui.label("Status").classes("text-sm text-gray-500")
                tree_status_badge = ui.label("Loading...").classes(
                    "px-3 py-1 text-sm font-semibold rounded-full bg-amber-100 text-amber-700"
                )
            tree_message_label = ui.label("Refreshing TreeBuilder coverage...").classes(
                "text-sm text-gray-600 mt-1"
            )
            tree_detail_label = ui.label("Inbox root: —").classes(
                "text-sm text-gray-500 mt-1"
            )
            tree_target_count_label = ui.label("Targets: —").classes(
                "text-sm text-gray-500 mt-1"
            )
            target_list_markdown = ui.markdown(
                "Targets will appear after the first refresh."
            ).classes("text-sm text-gray-600 mt-2 break-words")
            tree_error_label

    services_refresh_in_progress = False
    api_reachable = False
    services_timer: Any | None = None
    services_auto_refresh_enabled = False
    services_down_message_active = False
    SERVICE_DOWN_MESSAGE = (
        "Service not running\nStart backend services to populate this panel"
    )

    def _enable_services_auto_refresh() -> None:
        nonlocal services_auto_refresh_enabled
        if services_timer is None or services_auto_refresh_enabled:
            return
        services_auto_refresh_enabled = True
        services_timer.active = True

    def _disable_services_auto_refresh() -> None:
        nonlocal services_auto_refresh_enabled
        services_auto_refresh_enabled = False
        if services_timer is not None:
            services_timer.active = False

    def _set_badge(label: Any, state: str, stalled: bool) -> None:
        text, classes = _format_state_badge(state, stalled)
        _set_text_if_changed(label, text)
        _set_classes_if_changed(
            label, f"px-3 py-1 text-xs font-semibold rounded-full {classes}"
        )

    def _target_display(target: dict[str, str | None]) -> str:
        repo = target.get("full_repo") or target.get("repo") or "unknown"
        branch = target.get("branch") or "—"
        return f"{repo}:{branch}"

    def _update_watcher_card(
        status_payload: dict[str, Any],
        running_job: dict[str, Any] | None,
        last_job: dict[str, Any] | None,
    ) -> None:
        jobs_section = status_payload.get("jobs") or {}
        counts = jobs_section.get("counts") or {}
        running_total = int(counts.get("running") or 0)
        stalled_running = int(jobs_section.get("stalled_running") or 0)
        total_runs = int(jobs_section.get("total") or 0)
        runs_root_exists = status_payload.get("roots", {}).get(
            "runs_root_exists", False
        )
        detail_state = running_job or last_job
        detail_state_value = detail_state.get("state") if detail_state else None
        state_value = _normalize_state(detail_state_value)
        _set_badge(
            watcher_status_badge,
            state_value,
            stalled_running > 0 or bool((running_job or {}).get("stalled")),
        )
        status_text = detail_state_value or status_payload.get("status", "ok")
        _set_text_if_changed(watcher_status_detail, status_text.capitalize())
        heartbeat_value = (running_job or last_job) and (running_job or last_job).get(
            "heartbeat_at"
        )
        heartbeat_label = _format_timestamp_label("Last heartbeat", heartbeat_value)
        _set_text_if_changed(
            watcher_heartbeat_label, heartbeat_label or "Last heartbeat: —"
        )
        if not runs_root_exists:
            _set_text_if_changed(
                watcher_message_label,
                "Runs root missing; watcher cannot persist metadata.",
            )
        elif stalled_running:
            _set_text_if_changed(
                watcher_message_label, f"{stalled_running} stalled run(s)"
            )
        elif running_total:
            _set_text_if_changed(
                watcher_message_label, f"{running_total} running run(s)"
            )
        elif total_runs:
            _set_text_if_changed(watcher_message_label, "No active runs right now.")
        else:
            _set_text_if_changed(watcher_message_label, "No runs recorded yet.")
        runs_root = status_payload.get("config", {}).get("runs_root") or "unknown"
        _set_text_if_changed(watcher_detail_label, f"Runs root: {runs_root}")

    def _update_tree_card(
        status_payload: dict[str, Any], targets: list[dict[str, str | None]]
    ) -> None:
        roots = status_payload.get("roots") or {}
        config = status_payload.get("config") or {}
        summary = status_payload.get("targets") or {}
        root_exists = bool(roots.get("tree_builder_root_exists"))
        target_count = int(summary.get("count") or len(targets))
        _set_badge(tree_status_badge, "running" if root_exists else "unknown", False)
        if root_exists:
            _set_text_if_changed(
                tree_message_label,
                (
                    f"{target_count} target(s) discovered"
                    if target_count
                    else "Root exists but no targets discovered yet."
                ),
            )
        else:
            _set_text_if_changed(
                tree_message_label,
                "Configured inbox root is missing; TreeBuilder cannot sync.",
            )
        _set_text_if_changed(
            tree_detail_label,
            f"Inbox root: {config.get('tree_builder_root') or 'unknown'}",
        )
        _set_text_if_changed(tree_target_count_label, f"Targets: {target_count}")
        if targets:
            preview = targets[:_SERVICE_TARGET_PREVIEW]
            list_text = "\n".join(f"- {_target_display(target)}" for target in preview)
            _set_text_if_changed(target_list_markdown, list_text)
        else:
            _set_text_if_changed(
                target_list_markdown, "No inbox targets available yet."
            )

    async def _refresh_services() -> None:
        nonlocal services_refresh_in_progress, services_down_message_active
        if services_refresh_in_progress:
            return
        services_refresh_in_progress = True
        refresh_button.disabled = True
        _set_visibility_if_changed(watcher_error_label, False)
        _set_visibility_if_changed(tree_error_label, False)
        refresh_success = False
        try:
            status_payload = await client.get_status()
            services_down_message_active = False
            running_job: dict[str, Any] | None = None
            last_job: dict[str, Any] | None = None
            job_error: str | None = None
            try:
                running_jobs = await client.list_jobs(state="running", limit=1)
                if running_jobs:
                    running_job = running_jobs[0]
                    last_job = running_job
                else:
                    fallback_jobs = await client.list_jobs(limit=1)
                    if fallback_jobs:
                        last_job = fallback_jobs[0]
            except Exception as exc:  # noqa: BLE001
                job_error = f"Watcher runs unavailable: {exc}"
            _update_watcher_card(status_payload, running_job, last_job)
            _set_visibility_if_changed(watcher_error_label, bool(job_error))
            if job_error:
                _set_text_if_changed(watcher_error_label, job_error)
            targets: list[dict[str, str | None]] = []
            target_error: str | None = None
            try:
                targets = await client.list_targets()
            except Exception as exc:  # noqa: BLE001
                target_error = f"Failed to load targets: {exc}"
            _update_tree_card(status_payload, targets)
            _set_visibility_if_changed(tree_error_label, bool(target_error))
            if target_error:
                _set_text_if_changed(tree_error_label, target_error)
            refresh_success = True
        except Exception as exc:  # noqa: BLE001
            services_down_message_active = True
            error_text = f"Failed to fetch status: {exc}"
            _set_text_if_changed(watcher_error_label, error_text)
            _set_visibility_if_changed(watcher_error_label, True)
            _set_text_if_changed(tree_error_label, error_text)
            _set_visibility_if_changed(tree_error_label, True)
            _set_classes_if_changed(
                connectivity_hint_label,
                f"{CONNECTIVITY_HINT_BASE_CLASSES} text-rose-600",
            )
            _set_text_if_changed(connectivity_hint_label, SERVICE_DOWN_MESSAGE)
            _disable_services_auto_refresh()
        finally:
            services_refresh_in_progress = False
            refresh_button.disabled = services_refresh_in_progress or not api_reachable
            if refresh_success and api_reachable:
                now_label = _format_timestamp_label(
                    "Last refresh",
                    datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                )
                if now_label:
                    _set_classes_if_changed(
                        connectivity_hint_label,
                        f"{CONNECTIVITY_HINT_BASE_CLASSES} text-gray-500",
                    )
                    _set_text_if_changed(connectivity_hint_label, now_label)
                _enable_services_auto_refresh()
            elif not refresh_success:
                _disable_services_auto_refresh()

    def _on_connectivity_change(reachable: bool) -> None:
        nonlocal api_reachable
        previous_reachable = api_reachable
        api_reachable = reachable
        refresh_button.disabled = services_refresh_in_progress or not api_reachable
        if services_down_message_active:
            if not reachable:
                _disable_services_auto_refresh()
            elif not previous_reachable:
                _schedule_async(_refresh_services)
            return
        if reachable:
            _set_text_if_changed(connectivity_hint_label, "API reachable")
            _set_classes_if_changed(
                connectivity_hint_label,
                f"{CONNECTIVITY_HINT_BASE_CLASSES} text-emerald-600",
            )
        else:
            _set_text_if_changed(connectivity_hint_label, "API unreachable")
            _set_classes_if_changed(
                connectivity_hint_label,
                f"{CONNECTIVITY_HINT_BASE_CLASSES} text-rose-600",
            )
            _disable_services_auto_refresh()
        if reachable and not previous_reachable:
            _schedule_async(_refresh_services)

    services_timer = ui.timer(2.0, _refresh_services, active=False, immediate=False)
    if test_context is not None:
        services_panel_hooks = test_context.setdefault("services_panel", {})
        services_panel_hooks["connectivity_hint_label"] = connectivity_hint_label
        services_panel_hooks["watcher_status_detail"] = watcher_status_detail
        services_panel_hooks["refresh_services"] = _refresh_services
    register_connectivity_listener(_on_connectivity_change)
    _schedule_async(_refresh_services)


def create_ui_app(
    settings: UISettings | None = None,
    test_context: Dict[str, Any] | None = None,
) -> None:
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
            _set_text_if_changed(
                status_label,
                (
                    f"API reachable (v{report.version})"
                    if report.version
                    else "API reachable"
                ),
            )
        else:
            color = "text-red-500"
            detail = f" ({report.detail})" if report.detail else ""
            _set_text_if_changed(status_label, f"API unreachable{detail}")
        _set_classes_if_changed(status_icon, f"text-xl {color}")
        _set_classes_if_changed(status_label, f"font-medium {color}")

    _schedule_async(refresh_connectivity)
    ui.timer(5.0, refresh_connectivity)
    with ui.tabs().classes("w-full").props("pills") as tabs:
        ui.tab("Dashboard")
        ui.tab("Submit")
        ui.tab("Services")

    with ui.tab_panels(tabs, value="Dashboard").classes("w-full"):
        with ui.tab_panel("Dashboard"):
            _build_dashboard_panel(settings, client)
        with ui.tab_panel("Submit"):
            _build_submit_panel(
                settings, client, register_connectivity_listener, test_context
            )
        with ui.tab_panel("Services"):
            _build_services_panel(client, register_connectivity_listener, test_context)
