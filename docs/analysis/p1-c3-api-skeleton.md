# Phase 1 · Checkpoint 3 API Skeleton Analysis

## Layout & config surface
- The watcher (`scripts/codex_watcher.py`) and tree builder (`scripts/rebuild_inbox_tree.py`) already share a YAML config at `/srv/prompt-valet/config/prompt-valet.yaml` (override via `PV_CONFIG_PATH`). `load_config()` in the watcher normalizes defaults, ensures `pv_root` exists, and canonicalizes `inbox`, `processed`, `finished`, `repos_root`, `failed`, and the derived `runs` directory (`pv_root/runs`).
- No HTTP framework exists today; the new FastAPI app will live under a lightweight package, e.g. `prompt_valet/api/` with helpers in `config.py`, `discovery.py`, and `jobs.py`, exposed via `prompt_valet/api/app.py`. A thin CLI (`scripts/pv_api.py`) can import that app and drive `uvicorn.run(app, host=..., port=...)` so we keep the HTTP entrypoint separate from the watcher.
- Config for the API itself comes from env vars (see below) plus the same YAML config for `git_owner`/`inbox_mode` when discovery needs it.

## Discovery logic
- TreeBuilder writes inbox directories under the configured inbox root (default `/srv/prompt-valet/inbox`). Each repo root is `<inbox_root>/<repo_key>` and each branch is `<repo_key>/<branch>`. The watcher’s `resolve_prompt_repo()` knows two layouts: `legacy_single_owner` (legacy repo directories, owner from `git_owner`) and `multi_owner` (owner/repo/branch folders). Discovery will mimic that so the API can emit the same combinations.
- Discovery begins by resolving the configured root (`PV_REPOS_ROOT` or default `CONFIG["inbox"]`), scanning for `.pv_inbox` markers first (if present we treat the parent as a branch root). If no marker is found, we fall back to the folder names themselves (`<repo>/<branch>` or `<owner>/<repo>/<branch>`) and infer repo/branch metadata accordingly.
- Each target entry will include at least `{"repo": "<owner>/<repo>", "branch": "<branch>", "inbox_path": "<absolute path>"}` so clients can map directly to the watcher’s input tree.

## Job listing logic
- Job metadata is stored under `runs/<job_id>/job.json` (the watcher’s `JobMetadataWriter` writes payloads that always include `state`, `created_at`, `updated_at`, `started_at`, `heartbeat_at`, `log_path`, and `job_id`). The API walks `PV_RUNS_ROOT` (default `/srv/prompt-valet/runs`), reads `job.json` in each numeric directory, and skips anything that fails to parse or is missing the required file.
- `stalled` is derived from `heartbeat_at`: if a job is `state == "running"` (or any non-terminal state that implies live execution) and `heartbeat_at` is older than `now - PV_STALL_THRESHOLD_SECONDS` (default 60s), mark it stalled. Missing heartbeat timestamps are treated as not stalled (safer and consistent with atomic writes).
- `age_seconds` is computed as `now - datetime.fromisoformat(created_at or started_at)` (with a fallback to `started_at` when `created_at` is missing) so status/detail responses can surface run longevity without parsing client-side.
- The `/jobs` list supports the requested filters (`state`, `repo`, `branch`, `stalled`, `limit`) by filtering the parsed jobs in memory, and `/jobs/{job_id}` returns the enriched payload from a single file (including `stalled` and `age_seconds`).

## API surface
- `GET /api/v1/healthz` → `{status:"ok", version:"0.1.0"}` (version optional but we can keep a constant).
- `GET /api/v1/status` → config summary (tree-builder root, runs root, stall threshold, bind host/port), job counts (`running`, `succeeded`, `failed`, `aborted`, `queued` if present), stalled running count, optional hints like whether the runs and inbox roots exist.
- `GET /api/v1/targets` → list of flattened `{repo, branch, inbox_path}` entries derived from the tree builder layout.
- `GET /api/v1/jobs` → list of parsed job payloads with derived `stalled`/`age_seconds`, supporting filters/limit, ordered newest-first by `created_at`.
- `GET /api/v1/jobs/{job_id}` → single job detail (original `job.json` + derived fields). All endpoints stay read-only; no mutations.

## Config keys for the API
- `PV_REPOS_ROOT` (TreeBuilder/inbox root; default `/srv/prompt-valet/inbox`).
- `PV_RUNS_ROOT` (`pv_root/runs`, i.e. `/srv/prompt-valet/runs`).
- `PV_STALL_THRESHOLD_SECONDS` (default 60; used to flag stale heartbeats).
- `PV_BIND_HOST` / `PV_BIND_PORT` (defaults `127.0.0.1` / `8888` for `uvicorn`).
- The YAML config backing the watcher still matters for `git_owner` and `inbox_mode` so discovery can match the watcher’s assumptions.

## Derived-field summary
- `stalled`: `heartbeat_at` older than threshold while `state` implies running.
- `age_seconds`: time since `created_at` (fallback `started_at`).

This artifact finishes Block A; next we will implement the FastAPI skeleton plus helpers (Block B) without breaking the repo.
