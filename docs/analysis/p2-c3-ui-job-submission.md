# P2·C3 — UI Job Submission Analysis & Plan

## Repository surface inspection
- **API base**: FastAPI router under `prompt_valet/api/app.py` exposes `/api/v1/healthz`, `/status`, `/targets`, `/jobs`, `/jobs/{job_id}`, `/jobs/{job_id}/log`, `/jobs/{job_id}/log/stream`, `/jobs/{job_id}/abort`, `/jobs` POST, and `/jobs/upload` POST.
- **Job submission helpers** live in `prompt_valet/api/submissions.py`; Compose (JSON) and upload flows (`submit_job_from_upload`) share most validation/frontmatter logic.
- **UI client today** only wraps `healthz`, `/jobs`, and `/jobs/{job_id}` via `prompt_valet/ui/client.py`; `prompt_valet/ui/app.py` renders the dashboard and a placeholder submit card.

## API contract for submission
- **Compose** (`POST /api/v1/jobs`): expects JSON payload (`repo`, `branch`, `markdown_text`, optional `filename`). The backend validates repo/branch via `resolve_target`, normalizes/merges YAML frontmatter, enforces `.md`/`.prompt.md` suffixes, guarantees unique `job_id`-injected filename, writes atomically into the inbox, and replies with `{job_id, inbox_path, created_at}`. Errors: 400 for missing/invalid inputs or frontmatter, 404 when no matching inbox target, 409 on filename collisions after retries, 500 on IO issues.
- **Upload** (`POST /api/v1/jobs/upload`): multipart/form-data with `repo`, `branch`, and `files`. Each `UploadFile` must end with `.md` and be UTF-8; the handler calls the same submission helper per file and returns `{"jobs": [ {job_id, inbox_path, created_at}, ... ] }`. Errors: 400 if no files, a file is non-`.md`/non-decodable, or repo/branch invalid; 404/500 bubble from the shared helper.
- **Targets discovery** (`GET /api/v1/targets`): returns inbox metadata (`repo`, `branch`, optional `owner`/`full_repo`). The UI can use this to populate repo/branch selectors without touching the filesystem.
- **Health** (`GET /api/v1/healthz`): simple `{"status": "ok", "version": ...}` used by the header connectivity indicator; Compose/Upload must honour its reachable flag before enabling submissions.

## UI review & mapping
- **Existing UI** renders the dashboard plus a stubbed submit card that claims it will eventually hit `/api/v1/jobs` and `/api/v1/jobs/upload`. `prompt_valet/ui/client.py` already centralizes API calls, so the new Compose/Upload logic must extend this abstraction.
- **Compose mode mapping**:
  1. Query `/targets` to build repo/branch dropdowns (`full_repo` when available, fall back to bare repo).
  2. Collect markdown text, optional filename, and the selected repo/branch; disable the Submit button unless a valid target is chosen and the header reports `healthz` reachable (bonus: also require non-empty markdown for guardrails).
  3. Call the new `POST /jobs` client helper, surface job IDs/links on success, show the API error message otherwise, and reset the compose state/inputs as appropriate. Every API call must go through `PromptValetAPIClient`.
- **Upload mode mapping**:
  1. Reuse the same repo/branch selection state so Upload and Compose are consistent.
  2. Render a `ui.upload` control (`multiple=True`, `accept='.md'`) and store the `FileUpload` instances NiceGUI provides when `on_multi_upload` fires.
  3. Give users a “Submit files” button disabled while there are no uploads, the API is unreachable, or the repo/branch isn’t selected.
  4. When submitting, stream the stored `FileUpload`s directly through the new `PromptValetAPIClient.upload_jobs` helper so their bytes are passed untouched to `/api/v1/jobs/upload`; populate a results table that lists each filename, the returned job ID, and a link to the `GET /jobs/{job_id}` payload (or display the exception text when the upload call fails).
  5. After success or failure, clear the upload queue (`upload.reset()`) and stored files to avoid stale state.
- **Guardrails**: All HTTP interactions happen through `PromptValetAPIClient`; the UI never reads files itself and relies solely on API responses/state. Compose/Upload simply reflect what the API returns (job IDs, error strings) and do not speculate about job progress.

## Files impacted
1. `prompt_valet/ui/client.py` — extend `PromptValetAPIClient` with `list_targets()`, `submit_job(...)`, and `upload_jobs(...)` helpers so the UI code can keep device-agnostic API logic centralized.
2. `prompt_valet/ui/app.py` — replace `_build_submit_panel()` with the Compose/Upload UI, add state (targets, repo/branch selection, upload queue, health-ready flag, submission feedback), and reuse the existing header health indicator to gate button enabling.
3. `docs/analysis/p2-c3-ui-job-submission.md` — this document (analysis + plan).
4. Later in Block C: `PHASE_CHECKLIST.md` will get a new `P2·C3` entry.

## Immediate plan for Block B
1. Expand the API client abstraction before wiring the UI so Compose/Upload handlers can call `client.submit_job` and `client.upload_jobs` without duplicating HTTP logic.
2. Rebuild the Submit tab to include target selection, Compose card, Upload card (with file upload control and per-file feedback), and gating based on the header’s health ping.
3. Ensure Compose submission displays job IDs with links and Upload submission produces a results table per file; handle errors gracefully and reset controls after each run.
4. Maintain responsiveness via NiceGUI’s grid/row system (cards that shrink on small screens) and keep formatting consistent with existing styles.

Once Block B is complete, Block C will focus on running `pytest -q`, `ruff check .`, `black --check .`, manually sanity-checking the UI flows (note if manual checks aren’t feasible), updating `PHASE_CHECKLIST.md`, and documenting any limitations.
