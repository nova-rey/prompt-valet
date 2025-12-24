# Phase 2 · Checkpoint 5 UI Service Visibility Analysis

## 1. Phase 1 endpoints that can drive service visibility

### `GET /api/v1/healthz` (`prompt_valet/api/app.py:63-69`)
- request: simple GET, no body
- response: `{ "status": "ok", "version": "<__version__>" }`
- status codes: `200` on success; `4xx/5xx` only from HTTP layer
- usage: confirms the control plane is reachable (existing `PromptValetAPIClient.ping` already calls this and drives the header indicator)

### `GET /api/v1/status` (`prompt_valet/api/app.py:73-107`)
- request: GET with no parameters
- response:
  ```json
  {
    "status": "ok",
    "config": {
      "tree_builder_root": "...",
      "runs_root": "...",
      "stall_threshold_seconds": 60,
      "bind_host": "...",
      "bind_port": 8888
    },
    "jobs": {
      "counts": { "running": N, "succeeded": M, ... },
      "total": <int>,
      "stalled_running": <int>
    },
    "targets": { "count": <int> },
    "roots": {
      "tree_builder_root_exists": true|false,
      "runs_root_exists": true|false
    }
  }
  ```
- provides aggregate health info (job counts, stalled running count, whether the roots exist)
- this is the only coarse “service” endpoint today; the UI must derive Watcher/TreeBuilder visibility from the fields it exposes

### `GET /api/v1/jobs` (`prompt_valet/api/app.py:115-144`)
- request: query params `state`, `repo`, `branch`, `stalled`, `limit` (all optional)
- response: `{ "jobs": [ <job metadata objects> ] }`
- each job object is the contents of `runs/<job_id>/job.json` plus the derived booleans `stalled` and `age_seconds` (`prompt_valet/api/jobs.py`)
- the `stalled` field is the only staleness indicator Phase 1 defines for live Watcher work (see `docs/analysis/p1-c2-watcher-instrumentation.md`); it is computed from `heartbeat_at` versus `stall_threshold_seconds`
- the job metadata also surfaces `heartbeat_at`, `updated_at`, and `state`, so the UI can pick the freshest timestamps to show “Last heartbeat”

### `GET /api/v1/targets` (`prompt_valet/api/app.py:134-142`)
- request: GET, no parameters
- response: `[ { "repo": "...", "branch": "...", "inbox_path": "...", "owner": ..., "full_repo": ... }, ... ]`
- this mirrors the TreeBuilder discovery logic in `prompt_valet/api/discovery.py`
- there is no timestamp or staleness marker here—only the current branch list living in the inbox tree that the TreeBuilder maintains

### Restart controls
- No Phase 1 endpoints expose Watcher or TreeBuilder restart controls; restarting still happens outside the API (systemd unit at `systemd/prompt-valet-watcher.service` or `scripts/rebuild_inbox_tree.py` runs via a timer)
- therefore the Services tab can only display observable state; guard buttons with a note that there is no API if restart becomes possible later

## 2. Extracting service signals from the existing data
- **Watcher health:** the Watcher’s heartbeat thread updates each running job’s `heartbeat_at` and sets `stalled=True` when that timestamp exceeds `stall_threshold_seconds`. The API surfaces both the raw timestamp, the derived `stalled` flag, and the aggregated `stalled_running` counter in `/status`. The newest running job’s `heartbeat_at` gives us a “last heartbeat” timestamp; `stalled_running > 0` means the Watcher is currently writing but not refreshing heartbeats fast enough (and should be surfaced as “stalled” rather than a UI-only label). An empty `running` set simply means the Watcher is idle rather than offline.
- **TreeBuilder health:** the best we can show today is whether the configured inbox root exists (`roots.tree_builder_root_exists`) and how many targets `/targets` returns. There is no Phase 1 timestamp or “last sync at” field, so we cannot compute a derived staleness flag—TreeBuilder visibility remains limited to root existence and branch coverage counts.

## 3. Current Services tab + connectivity wiring
- `_build_services_panel()` (`prompt_valet/ui/app.py:1015-1031`) currently renders static copy (“Service wiring” + explanatory text) and no API data. There are no consumer controls, cards, or refresh actions.
- Header connectivity logic (`prompt_valet/ui/app.py:1054-1080`) already calls `PromptValetAPIClient.ping()` every 5 s, keeps `api_connectivity_reachable`, and notifies listeners via `register_connectivity_listener`. The Submit tab partners with that via `register_connectivity_listener`. The new services panel must reuse the same reachability signal so its refresh button (and any card state) greys out when the API is unreachable.

## 4. UI mapping rules for service cards
- **Card data from Phase 1 definitions only:**
  - Status text must come from `state`/`stalled`/`status` fields that the API already defines; no custom “online/offline” enums.
  - “Stale” is only calculated when `stalled` or `heartbeat_at` exists (for the Watcher card). TreeBuilder cards do not show a stale badge because the API provides no timestamp.
  - Color mapping reuses the badge palette already defined for job states (`_STATE_BADGE_STYLES`)—e.g., “running” uses the blue palette, “failed/aborted” use red/stone, “unknown” falls back to amber.
- **Watcher card:**
  - Status: show `running`, `queued`, or `failed` depending on the most recent non-terminal job state (fall back to `status` from `/status` when there are no jobs). If any running job has `stalled == true`, display “Running (stalled)” and treat it as a `stalled` marker (same display as dashboard rows).
  - Last heartbeat: format the freshest `heartbeat_at` timestamp from running jobs; fall back to `updated_at` if no heartbeat is available.
  - Message/reason: surface `/status` → `jobs.stalled_running` count, e.g., “Stalled runs: N”, or note “No runs yet” when the runs root is empty.
  - Color: use the same classes as `_format_state_badge` would for a `running` state (blue) or `unknown` when the API is unreachable.
- **TreeBuilder card:**
  - Status: when `roots.tree_builder_root_exists` is `false`, show “Missing inbox root” with the `unknown` palette; otherwise use “ok”/“running” (blue) to signal the tree exists.
  - Last update: not available—show the total `targets.count` plus a list of the first few repos/branches returned by `/targets` to prove recent discovery.
  - Message: highlight the inbox root path (from `/status` → `config.tree_builder_root`) and the number of targets; if `/targets` returns an empty list while the root exists, note “No branches discovered yet”.
  - No restart button because no endpoint exists today.

## 5. Implementation plan (Blocks B+C)
1. **API client updates:** extend `PromptValetAPIClient` with
   - `async def get_status() -> dict[str, object]` to call `/status`,
   - an optional-parameter version of `list_jobs` (reuse existing method signature) so the services tab can request `state=running`/`limit=1` when needed, and
   - (optionally) a helper that summarizes `/targets` results (the existing `list_targets` method already works but may benefit from a lightweight wrapper that caches the recent response for the Refresh button).
   Add unit tests covering the new helpers (use `httpx.MockTransport` to assert the right URLs/params and to simulate errors).
2. **Services panel UI:**
   - Replace `_build_services_panel()` with two responsive cards (`Watcher` and `TreeBuilder`) plus an explicit “Refresh services” button that re-queries `/status`, `/jobs` (filtered to running or the most recent job), and `/targets`.
   - Use the header’s connectivity listener (or `api_connectivity_reachable`) to disable the refresh button and show a tooltip/error when the API is unreachable. Each card should show a spinner while its section is loading and an inline error message when the response is invalid.
   - Format timestamps using the existing `_format_timestamp` helpers; reuse badge styling logic from `_format_state_badge`.
   - Display message text only when the API supplies data (`stalled_running` count, `targets.count`). Do not invent extra reasons.
   - Keep the layout touch-friendly (stack on narrow screens) by leveraging `ui.row`/`ui.column` with `wrap`. Buttons should be `ui.button` with clear labels.
3. **Error handling & degradation:**
   - When any API call fails (status not 200 or JSON parse error), the affected card shows a clear message (e.g., “Failed to fetch watcher status: {error}”). If the entire Services tab cannot load, the Refresh button remains visible so operators can retry manually.
   - If `/status` reports `runs_root_exists == false`, surface that as a warning (“Runs root missing”); this matches Phase 1 definitions for root availability.
4. **Testing & verification (Block C prep):**
   - Add unit tests for the new client helpers and for the service-card response handling (mock the API responses to cover healthy/stalled/missing-root cases).
   - After implementation run `pytest -q`, `ruff check .`, and `black --check .` (per Block C instructions). Fix failures and rerun until everything passes.
5. **Documentation updates:**
   - Keep this analysis doc updated and add another paragraph to `PHASE_CHECKLIST.md` marking P2·C5 complete with a one-line summary once the UI is shipped.
   - Mention in the final summary whether restart controls were left out (and why).

## Next steps
Execute Block B (implement the Service Visibility UI) followed by Block C (tests + checklist + summary). No new backend endpoints are required—reuse `/status`, `/jobs`, `/targets`, and `healthz`.
