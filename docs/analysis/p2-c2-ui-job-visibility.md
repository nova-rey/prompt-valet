# Phase 2 · Checkpoint 2 UI Job Visibility Analysis

## 1. Existing UI layout & API client wiring
- `prompt_valet/ui/app.py` currently builds the NiceGUI shell: header with connectivity indicator, `ui.tabs` for Dashboard/Submit/Services, and a `ui.tab_panel` placeholder in the Dashboard tab. `create_ui_app()` constructs the shared `PromptValetAPIClient` (currently only `ping()` via `/api/v1/healthz`).
- There are no consumers of `/api/v1/jobs` or `/api/v1/jobs/{job_id}` yet; the Dashboard tab only shows static cards and explanatory text. The `PromptValetAPIClient` is the natural place to introduce the new job-fetch helpers so all API traffic stays centralized.

## 2. View structure for job visibility
1. **Dashboard (Jobs list)**
   - Replace the existing placeholder cards with a jobs panel that includes the connectivity status, a `Refresh` button, and a sortable table keyed by `created_at` (descending by default).
   - Columns: Job ID, Repo, Branch, State (with visual badge), Time (created/started), Heartbeat / stalled indicator, Exit code (when terminal), and a chevron/link column to open details.
   - Empty state: show a friendly message (“No jobs yet. Check back later.”) when the API returns an empty list.
2. **Job Detail panel**
   - Triggered by selecting a job row (link/icon/button that opens a NiceGUI `card` or `dialog` inside the Dashboard tab).
   - Fetch `/api/v1/jobs/{job_id}` on demand and show the raw metadata plus derived fields (timestamps, `stalled`, `age_seconds`).
   - Include disabled buttons or links labeled “Logs (coming later)” and “Abort (coming later)” to signal future work without invoking actions.

## 3. Data flow diagram
1. App startup loads `UISettings` and instantiates `PromptValetAPIClient` (used already for ping).
2. Dashboard tab triggers `refresh_jobs()` on load and from a `ui.timer` (10 s interval) or manual `Refresh` action.
3. `refresh_jobs()` calls `PromptValetAPIClient.list_jobs()` → `GET /api/v1/jobs` → returns array of enriched job dicts (payload + `stalled`, `age_seconds`).
4. Selected job row triggers `PromptValetAPIClient.get_job_detail(job_id)` → `GET /api/v1/jobs/{job_id}` and populates the detail panel.
5. All HTTP requests are wrapped in async handlers so the header connectivity timer and job data stay independent but share the same client.

## 4. Polling & refresh strategy
- **Auto refresh:** `ui.timer(10, refresh_jobs, on_start=True)` keeps the list fresh while avoiding aggressive polls. 10 s mirrors the current health ping cadence and gives watchers time to update heartbeats.
- **Manual refresh:** add a `ui.button("Refresh")` so operators can recover faster if an earlier request failed or when they suspect new jobs.
- **Backoff:** keep the timer running even after a failure but avoid stacking requests by checking `refresh_in_progress` flag; repeated failures display an inline error but do not accelerate polling.

## 5. Error handling & UI state model
- **States:**
  - `loading`: spinner/card shown while awaiting `GET /api/v1/jobs`.
  - `ready`: jobs table populated; supports sorted list and detail navigation.
  - `empty`: API returned no jobs; show an informative placeholder within the same card.
  - `error`: network/HTTP error from `list_jobs()` renders a warning banner in the panel (and optionally `ui.notify`). The prior successful data remains visible until new data arrives.
- **Partial data:** API delivers a full list atomically, so the UI never shows partial rows. Should `PromptValetAPIClient` detect malformed job entries, it will surface an error and keep the last known good dataset.
- **API unreachable:** The same banner/co-messaging used by the connectivity indicator can mention that `/api/v1/jobs` failed. The table stays disabled (greyed out) and the next timer tick retries.
- **Job detail errors:** Display an inline message in the detail card & keep the job list accessible; detail fetch failures should not break the parent panel.

## 6. Field mapping (API → UI)
| UI column/field | Source data | Notes |
| --- | --- | --- |
| Job ID | `job_id` | Primary key; use monospace/copy-to-clipboard affordance in table & detail.
| Repo | `git_owner` + `/` + `repo_name` (fall back to `repo_name` only) | Derived so owner-info is visible.
| Branch | `branch_name` | Display verbatim; detail view shows the same plus fallback text when blank.
| State | `state` | Render badge with normalized class (running/succeeded/failed/aborted/stalled). Treat `failed_retryable` & `failed_final` both as `failed`. Running jobs that trigger `stalled=True` get a “Stalled” badge or icon.
| Created/Started | `started_at` (if present) else `created_at` | Show as `YYYY-MM-DD HH:MM:SS UTC` plus text about which timestamp is shown.
| Heartbeat/stall | `heartbeat_at` + derived `stalled` | If `heartbeat_at` exists, show “HB 12s ago”; if `stalled` true, show a red “Stalled” tag instead of the age.
| Exit Code | `payload.get("exit_code")` | Show only for terminal states (`succeeded/failed/aborted`); display `—` otherwise.
| Metadata table | entire `job.json` payload | Render as key/value list (sorted) plus derived `age_seconds` and `stalled` flag for clarity.

## 7. Derived fields handling
- **Stalled flag:** API adds `stalled` bool when a running job exceeds `UISettings.stall_threshold_seconds`. Use it to switch the heartbeat display to a prominent red “Stalled” indicator and to mark the state badge.
- **Heartbeat age:** `heartbeat_at` is used to compute “HB NN s/min ago” by comparing to `datetime.utcnow()` during rendering or by formatting `age_seconds` from the payload.
- **Age field:** `age_seconds` is already provided with `list_job_records`; show this in detail as “First activity Xs ago” so operators understand timing even if heartbeat is absent.

## 8. Explicit non-goals for P2·C2
- No job submission or uploads from the UI (Submit tab remains informational).
- No abort/log actions; any link/buttons for those are disabled and clearly labeled “coming later.”
- No filesystem access (UI consumes only `/api/v1/*`).
- No direct Watcher polling (rely solely on `/api/v1/jobs`).
- No API schema changes or new endpoints; consume the existing `/api/v1/jobs` and `/api/v1/jobs/{job_id}`.
- No streaming log views or SSE abstractions; detail just shows metadata + placeholders.

