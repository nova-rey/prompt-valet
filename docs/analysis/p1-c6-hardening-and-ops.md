# Phase 1 · Checkpoint 6 — Hardening and Ops Analysis

## Service topology
- **Watcher**: the long-lived agent is `scripts/codex_watcher.py`. It boots from `main()`, loads `/srv/prompt-valet/config/prompt-valet.yaml` (falling back to `DEFAULT_CONFIG` in `scripts/codex_watcher.py`), and polls the inbox tree under `DEFAULT_PV_ROOT / inbox` (with friends like `processed`, `finished`, `repos`, `failed`, and `runs`). The watcher can also be invoked with `--once` for debugging but normally runs as a systemd service (`systemd/prompt-valet-watcher.service`) that watches for `<name>.prompt.md` files, reports heartbeat/abort markers, and writes `runs/<job_id>/job.json` metadata.
- **API control plane**: `scripts/pv_api.py` imports `prompt_valet.api` to expose FastAPI over uvicorn. It materializes `APISettings` by reading the shared YAML config (via `PV_CONFIG_PATH`) and optional overrides for roots, ports, and stall thresholds. The API surfaces `/api/v1/healthz`, `/status`, `/jobs`, `/jobs/{id}`/log/stream, `/jobs/{id}/abort`, `/jobs` submission, and upload endpoints.

## Configuration and environment inventory
- **Config file**: `/srv/prompt-valet/config/prompt-valet.yaml` is the canonical config for watcher + tree builder + API. `scripts/codex_watcher.py` merges it with `DEFAULT_CONFIG` (which hard-codes `/srv/prompt-valet` roots, queue settings, and `watcher` defaults like `git_default_owner=\"nova-rey\"`, `git_default_host=\"github.com\"`, `runner_cmd=\"codex\"`, etc.). Operators can override the location via `PV_CONFIG_PATH`, which is also respected by the API and tree builder.
- **Environment overrides (API service)**:
  - `PV_REPOS_ROOT` (default: `CONFIG[\"inbox\"]` or `/srv/prompt-valet/inbox`): the TreeBuilder/inbox root that the API exposes via `/targets`.
  - `PV_RUNS_ROOT` (default: `pv_root/runs`, i.e. `/srv/prompt-valet/runs`): where watcher writes per-job metadata/logs and where the API reads job records.
  - `PV_BIND_HOST` / `PV_BIND_PORT` (defaults `127.0.0.1` / `8888`): uvicorn bind address for the API.
  - `PV_STALL_THRESHOLD_SECONDS` (default 60): how old a `heartbeat_at` timestamp must be before the API reports a job as stalled.
  - `PV_CONFIG_PATH`: the optional path to the YAML config (defaults to `/srv/prompt-valet/config/prompt-valet.yaml`).
- **Hard-coded roots**: `scripts/codex_watcher.py` relies on `DEFAULT_PV_ROOT = Path("/srv/prompt-valet")` and derives `inbox`, `processed`, `finished`, `repos`, `failed`, `runs` from it unless the YAML supplies alternatives. `scripts/pv_api.py` inherits those defaults via the merged config.

## Restart expectations
- **Watcher service** already operates with `Restart=on-failure` (see `systemd/prompt-valet-watcher.service`). On crash or unhandled exception it will be restarted automatically; reboots bring it up when the unit is enabled.
- **API service** must also `Restart=on-failure` with a short backoff so a transient failure (e.g., missing `uvicorn`) does not keep the service down. Systemd should reload environment before starting and deliberately not enable the service by default per P1·C6 instructions; operators will `systemctl start/stop prompt-valet-api.service` manually. Both services ultimately rely on the filesystem layout under `/srv/prompt-valet` and `/srv/repos`, so reboot semantics align with systemd: ensure the directories exist and the YAML config is present before letting the units run.

## Acceptance checklist definition
1. Start services in order (watcher already existing; API service deployed). Expect: `systemctl status prompt-valet-watcher.service` shows `active (running)` and `prompt-valet-api.service` reaches `active (running)` after `systemctl start` with no immediate crashes.
   - Date/operator: ______________________
2. Submit a job via `POST /api/v1/jobs` (or upload). Expect: API returns `201` with `job_id`, a `.prompt.md` appears under the configured inbox, and a matching `runs/<job_id>/job.json` file is created.
   - Date/operator: ______________________
3. Observe job state via `GET /api/v1/jobs/{job_id}` or `GET /api/v1/jobs`. Expect: the job transitions through `created` → `running` and eventually hits a terminal state (`succeeded`/`failed`/`aborted`). `runs/<job_id>/job.json` reflects the same state and heartbeat entries.
   - Date/operator: ______________________
4. Stream/log tail the job via `/api/v1/jobs/{job_id}/log` or `/log/stream`. Expect: log output from Codex appears, SSE stream stays alive while the job is non-terminal, and `journalctl`/`runs/<job_id>/job.log` contain the same entries.
   - Date/operator: ______________________
5. Abort the job via `POST /api/v1/jobs/{job_id}/abort`. Expect: watcher observes the `ABORT` marker, the job transitions to aborted, and subsequent `GET` calls reflect the terminal state while the stream closes gracefully.
   - Date/operator: ______________________
6. Verify the terminal side effects: processed prompt moves to `/srv/prompt-valet/finished`, the ingest metadata shows the final state, and `GET /api/v1/jobs?stalled=true` returns zero unless an actual stall occurred. Record the operator and date for each acceptance step.
   - Date/operator: ______________________

## Explicit non-goals (per P1·C6 guardrails)
- No new API endpoints beyond the existing FastAPI routes.
- No schema changes (job metadata schema remains as written by `JobMetadataWriter`).
- No watcher refactors—existing loops, queue semantics, and git housekeeping stay untouched.
- No scheduling or policy logic changes; the watcher still only claims prompts when seen on disk.
- No authentication enforcement or UI work.

## Deployment artifact decision
A systemd unit file is the simplest orchestrator for the API service—`scripts/pv_api.py` does not yet run in containers, and there is already an operational precedent for systemd units (watcher, tree builder, optional Copyparty). Therefore Block B will add `ops/systemd/prompt-valet-api.service` plus an env file template that ships the necessary `PV_*` overrides.
