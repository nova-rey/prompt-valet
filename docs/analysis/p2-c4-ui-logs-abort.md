# Phase 2 · Checkpoint 4 UI Logs & Abort Analysis

## 1. Phase 1 endpoints (available now)
- `GET /api/v1/jobs/{job_id}/log` (`prompt_valet/api/app.py:197-219`) tails the job log file, accepts `lines` (default 200), and always streams back UTF-8 `text/plain`. The server uses `_tail_file` and a fixed chunk size, so we can safely request the default tail every time the detail view opens or when users hit a “Refresh” control.
- `GET /api/v1/jobs/{job_id}/log/stream` (`prompt_valet/api/app.py:209-226`) returns a `text/event-stream` from `_stream_job_log_generator`. Each SSE payload is one line prefixed with `data: {payload}` and separated by a blank line; the connection yields nothing while waiting, polls every 0.5 s, and closes once the job record becomes terminal or the log file disappears. There is no custom `event` type, so the UI only needs to read the `data` field.
- `POST /api/v1/jobs/{job_id}/abort` (`prompt_valet/api/app.py:221-255`) accepts abort requests only when the job state is exactly `running`. On success it atomically writes an `ABORT` file beside the job log, and returns `job_id`, `previous_state`, and `abort_requested_at`. Non-running jobs raise 409 with the current state, so the UI must re-fetch job detail afterward to observe whether the backend marked it aborted.

## 2. Existing job detail UI surface
- `prompt_valet/ui/app.py:371-419` builds the Dashboard panel with a refresh timer, connectivity-aware refresh button, and the `jobs_table`. Table rows can be selected via `on_select`, which calls `_handle_job_selection` → `_show_job_detail` to unwrap the detail dialog (lines ~295-325).
- The dialog (`prompt_valet/ui/app.py:326-369`) is a `ui.card` housing the job title, state badge, heartbeat/age labels, timestamp list, metadata table, and two placeholder buttons (Logs/Abort). All requests (`list_jobs`, `get_job_detail`) already go through `PromptValetAPIClient` (`prompt_valet/ui/client.py:1-142`), so new log/abort helpers should land in the same class.
- The dialog is opened per-selection and closes automatically after `jobs_table.selected.clear()` so any new log UI must survive repeated opens (ideally by resetting state when the dialog closes or a new job is selected).

## 3. Behavior contract for Job Detail Logs + Abort
1. **Historical logs (“Recent Logs” panel)**
   - When the dialog opens, call the log tail endpoint with the default line count and render the text in a responsive, scrollable block (`ui.card`/`ui.textarea` with `wrap` and `selectable`). This panel should be readable on mobile (stacked layout, sane font sizing) and offer a manual “Refresh logs” control that reissues the tail request.
   - The tail text should accumulate the most recent lines and stay focused near the end after each refresh so operators see the latest activity.
2. **Live logs (SSE overlay)**
   - Provide a “Live Logs” toggle or button that connects to `/jobs/{job_id}/log/stream` when enabled. Append SSE `data` messages incrementally (one per log line) to the displayed log text while preserving order.
   - Maintain a separate “Pause/Disconnect” control that explicitly stops the SSE listener without touching the backend. Re-enabling “Live Logs” should reconnect, ideally reusing the previously fetched tail to avoid duplicates by either trimming to the last known content or fetching the tail again before appending incoming lines.
   - Implement auto-reconnect on transient connection failures: after a disconnect, retry with exponential backoff (e.g., 0.5 s → 1 s → 2 s → 4 s, cap at ~10 s) while the dialog is open and the toggle remains enabled. Reset the backoff when the stream successfully resumes. Automatically recover if the browser tab sleeps/resumes or the API restarts; detect dead streams (no data/heartbeat) and kick off another connection attempt.
   - The live stream should stop (and optionally show “Job completed” text) when the SSE connection closes because the backend marked the job terminal; do not treat this as an error—the UI should rely on the backend job detail to know the final state.
3. **Abort control**
   - The Abort button sits alongside the log controls but remains disabled unless `state_lower == "running"` (per `prompt_valet/ui/app.py:245-267`, this is derived from the job payload). When enabled, clicking it opens a modal requiring the operator to type `ABORT` (exact match) before issuing the `POST /jobs/{job_id}/abort` call through `PromptValetAPIClient`.
   - Once the API call succeeds, show an “abort requested” badge/message and keep polling `client.get_job_detail(job_id)` or waiting for the job list timer to observe the actual terminal state—do not assume the job became `aborted` immediately. If the abort endpoint returns 409 (`previous_state` in payload), display the backend state and keep the button disabled until the job re-enters `running` (unlikely but theoretically possible). Errors while calling abort should show inline feedback without closing the dialog.
   - Any state transitions (including `running → aborted/failed`) must come from `get_job_detail` or the job table refresh; the UI must not synthesize an instant terminal state.
4. **General UX**
   - Maintain responsive layout: log panels should stack vertically or use NiceGUI’s `ui.row`/`ui.column` classes with `flex-wrap` so mobile screens show all controls without horizontal scroll. Use visual badges/labels for SSE status (“Live”), reconnection attempts (“Reconnecting…”), and abort state (“Abort requested”).
   - Keep manual “Refresh logs” and “Live Logs” toggles reachable for both mobile and desktop, and show simple status text for SSE/backoff.

## 4. Implementation plan (Block B prep)
- Extend `PromptValetAPIClient` (`prompt_valet/ui/client.py`) with:
  1. `async def tail_job_log(job_id: str, lines: int | None = None) -> str` that calls `/jobs/{job_id}/log` with query params and returns the raw string.
  2. `async def stream_job_log(job_id: str)` which exposes an async generator/async iterator over the SSE `data` lines via `httpx.AsyncClient.stream("GET", url)` and yields decoded strings.
  3. `async def abort_job(job_id: str) -> dict[str, str]` wrapping the POST while surfacing `previous_state` and timestamps.
  Include tests for these helpers (e.g., using `httpx.MockTransport` or `respx`) to validate request URLs, parameters, and error handling.
- Augment the job detail dialog:
  1. Introduce a `ui.card` or `ui.expansion` for “Recent Logs” that contains a scrollable `ui.textarea`/`ui.markdown` block populated by `tail_job_log`, plus a “Refresh logs” button.
  2. Add controls (toggle + pause button) to start/stop SSE. Hook `asyncio.create_task` to manage the SSE consumer, hold onto `asyncio.Event`/`bool` to track running state, and update UI elements text (`Live logs active`, “Reconnecting in Xs”) for visibility.
  3. Merge the SSE output into the same text block (or a sibling panel) while preventing layout shifts on mobile.
- Add the abort flow:
  1. Replace the placeholder “Abort” button with one that checks `state_lower` to enable/disable. Clicking opens a `ui.dialog` that requires typing `ABORT` (maybe via `ui.input` bound to a validation) and then calls `PromptValetAPIClient.abort_job`.
  2. After success, show a temporary label (“Abort requested at …”) and keep re-fetching job detail to surface the backend state.
  3. Ensure dialogs share state so new job selections cancel active SSE tasks/abort states.
- Ensure reactivity:
  - When a new job detail is rendered, cancel existing SSE tasks before starting new ones to avoid log wires leaking between jobs.
  - Tie `jobs_table` selection clearing and dialog closure to SSE shutdown and log refresh resets.
- Add unit tests (likely in `tests/test_ui_formatting.py` or new file) verifying that the UI functions controlling SSE/backoff/abort guard behave as expected; use mocks/stubs for `PromptValetAPIClient` to simulate API responses (historical log string, SSE line stream, abort responses, errors).

## 5. Verification & constraints (Block C reminders)
- After Block B, run `pytest -q`, `ruff check .`, and `black --check .` as required by the checkpoint.
- Document SSE reconnect behavior (e.g., backoff states, pause/resume expectations) in the final summary.
- Backend constraints uncovered:
  - Abort only succeeds if the job is `running`; other states return 409 and must be surfaced to the operator. (`prompt_valet/api/app.py:224-245`)
  - SSE generator polls every 0.5 s and closes once `TERMINAL_STATES = {"succeeded", "failed", "aborted"}` is reached (`prompt_valet/api/app.py:86-114`). It also checks for missing log files and uses the latest job record to decide whether to continue streaming.
  - Historical log tail keeps only the last `lines` entries, so the UI must re-fetch if the user wants to see older outputs after reconnection.

## 6. Next steps
1. Build the client helpers and log UI inside the job detail dialog (Block B).  
2. Extend tests for the client helpers and log formatting/flow.  
3. After implementation, update `PHASE_CHECKLIST.md` (mark P2·C4) and confirm required commands finish cleanly.
