# Phase 2 · Checkpoint 1 NiceGUI UI Skeleton Analysis

## Deep scan & repo insights
- `prompt_valet/api` is the sole current Python package that exposes the FastAPI control plane; `prompt_valet/api/app.py` wires `/api/v1` routers for health, status, targets, jobs, log tail/stream, abort, and submission, and `scripts/pv_api.py` is the CLI that boots it via `uvicorn` while `prompt_valet/api/config.py` folds in `PV_*` env overrides.
- The repo already bundles tooling conventions via `pyproject.toml`: `fastapi`, `uvicorn`, `pyyaml`, and `python-multipart` are runtime deps while `[project.optional-dependencies]` brings in `ruff`, `black`, `pytest`, and (today only for tests) `httpx`; consistent `line-length = 88` is enforced across `black` and `ruff`.
- System automation lives under `systemd/` (e.g., `prompt-valet-tree-builder.service`/`.timer`), and helper scripts such as `scripts/rebuild_inbox_tree.py` target that tree builder role, so new services typically gain a `scripts/` CLI entrypoint plus any necessary systemd unit.
- Testing or build metadata is concentrated in `tests/` (Pytest suites covering submissions, logs, watchers, inbox builds) and `docs/ops/phase1-operator-guide.md` already documents how to install dependencies, deploy the API service, and exercise every `/api/v1/*` interaction.

## Phase 1 API surface details
- Base prefix: `/api/v1` across all FastAPI routes (health/status/targets/jobs and submissions), so any UI client can point to a single base URL.
- Endpoints include:
  - `GET /healthz` for heartbeat.
  - `GET /status`, `GET /targets`, `GET /jobs`, `GET /jobs/{job_id}` for metadata.
  - `GET /jobs/{job_id}/log` for tailing, and `GET /jobs/{job_id}/log/stream` which returns a `StreamingResponse` yielding SSE `data:` lines until the job transitions to a terminal state.
  - `POST /jobs`, `POST /jobs/upload`, and `POST /jobs/{job_id}/abort` for control-plane mutators.
- Log streaming is implemented with `StreamingResponse` plus an async iterator that reads `job.log`, emits `data:` payloads, sleeps 0.5s when idle, and stops once the job reaches a terminal state (succeeded/failed/aborted), so the UI can rely on SSE-friendly text/event-stream responses.
- No shared HTTP client utilities exist yet; the repo only specifies optional `httpx` for FastAPI tests and never exposes a reusable wrapper, so the UI must ship its own lightweight client.

## Tooling + service conventions
- Formatting/linting standard: `ruff` and `black` with line length 88 plus `pytest` for automated verification (all referenced in `pyproject.toml`).
- CLI pattern: scripts under `scripts/` (e.g., `scripts/pv_api.py`, `scripts/rebuild_inbox_tree.py`) import from `prompt_valet.*` and then call `uvicorn.run` or similar; the UI should follow that entrypoint style.
- Systemd placement: service units referenced under `systemd/` such as `prompt-valet-tree-builder.service`, meaning any future operator-facing services should live in `systemd/` when we add units.

## UI service placement decision
- The backend code lives under `prompt_valet/api`, so mirroring that convention recommends creating a `prompt_valet/ui` package that encapsulates settings, the API client, and the NiceGUI layout.
- The user-visible entrypoint for the API is `scripts/pv_api.py`, so the UI entrypoint should live under `scripts/pv_ui.py`, exposing the NiceGUI app via `nicegui.run` with host/port drawn from the new UI settings.
- With the FastAPI service already documented in `docs/ops/phase1-operator-guide.md`, adding a short UI section there keeps operators aware of both services and gives us a natural place to mention the new CLI and env overrides.

## Implementation plan
1. `pyproject.toml` – add `nicegui` and `httpx` to runtime dependencies so the UI client and layout can import them without extra instructions.
2. `prompt_valet/ui/settings.py` – define a `UISettings` dataclass that reads env vars such as `PV_API_BASE_URL`, `PV_UI_BIND_HOST`, `PV_UI_BIND_PORT`, and `PV_UI_API_TIMEOUT_SECONDS`, falling back to sensible defaults (e.g., `http://127.0.0.1:8888/api/v1`, `0.0.0.0`, `8080`, 5s).
3. `prompt_valet/ui/client.py` – implement an async `PromptValetAPIClient` using `httpx.AsyncClient`, providing `async ping()` with timeout/error handling plus sanitized base URL handling for `healthz`.
4. `prompt_valet/ui/app.py` – build the NiceGUI layout: header with a connectivity indicator that polls `prompt_valet.ui.client.PromptValetAPIClient.ping`, tabs for Dashboard/Submit/Services, responsive card rows, and placeholder copy indicating the API is reached via the client.
5. `prompt_valet/ui/__init__.py` – expose `create_ui_app`/`UISettings` so other modules (e.g., the CLI) can import them cleanly.
6. `scripts/pv_ui.py` – new CLI that loads `UISettings`, wires `create_ui_app`, and calls `nicegui.run` with the configured bind host/port.
7. `docs/ops/phase1-operator-guide.md` – add a short section describing the UI service, how to run `scripts/pv_ui.py`, and what env vars control API connectivity so operators know it is available.
8. `PHASE_CHECKLIST.md` – append a Phase 2 checkpoint line describing the NiceGUI service skeleton so the tracker reflects the new work.

Following this Block A analysis doc, continue straight into Block B to implement the described modules and docs (per prompt instructions).
