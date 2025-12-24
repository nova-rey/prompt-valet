# Phase 2 · Checkpoint 7 Ops Packaging Baseline

## Repository scan highlights
- systemd units: Phase 1 already ships `ops/systemd/prompt-valet-api.service` and the top-level `systemd/` folder keeps the watched/tree builder services, so the repo convention is to place human-editable units under `ops/systemd/` and copy them into `/etc/systemd/system/` at install time.
- env templates: `ops/env/pv.env.example` seeds the API wiring, `/etc/prompt-valet/pv.env` is the configured file, and operators currently source it for `prompt-valet-api.service`.
- operator docs: `docs/ops/phase1-operator-guide.md` describes API deployment, health checks, job submission, and the current (manual-only) UI section.
- phase docs layout: analysis lives under `docs/analysis/`, the roadmaps live in `docs/phase-roadmap.md` and `docs/Phase_Roadmap.md`, and the existing Phase 2 debrief sits at `docs/Phase2_Debrief.md`.

## UI service details
- entrypoint: `./scripts/pv_ui.py` boots the NiceGUI app by loading `UISettings`, creating the UI shape, and calling `nicegui.run` on `PV_UI_BIND_HOST`/`PV_UI_BIND_PORT`.
- required env vars (defaults from `prompt_valet/ui/settings.py`):
  - `PV_API_BASE_URL` (default `http://127.0.0.1:8888/api/v1`)
  - `PV_UI_BIND_HOST` (`0.0.0.0`)
  - `PV_UI_BIND_PORT` (`8080`)
  - `PV_UI_API_TIMEOUT_SECONDS` (`5.0`)
  - `PV_UI_USER` (new, will mirror `prompt-valet` user to drop privileges in systemd)

## Block B plan
1. Add a dedicated UI env template under `ops/env/` (`pv-ui.env.example`) that documents the vars above plus the systemd user, hints about copying to `/etc/prompt-valet/pv-ui.env`, and matches the API template's style.
2. Create `ops/systemd/prompt-valet-ui.service` that sources the new env file, drops to `${PV_UI_USER}`, runs `python3 /srv/prompt-valet/scripts/pv_ui.py`, mirrors the API service's restart policy, and installs alongside the other units.
3. Update `docs/ops/phase1-operator-guide.md` so the UI section now covers manual startup env overrides, the new systemd unit installation/details, connectivity verification, and troubleshooting cues.
4. Capture the Phase 2 happy-path baseline in `docs/phase2/baseline.md` (matching the new debrief directory introduced in Block B) with steps for running UI+API, verifying submissions/logs, and noting UI failures do not stop execution.
5. Add `docs/phase2/phase-debrief.md` that records what Phase 2 delivered and ties back to the Phase 2 roadmap/analysis artifacts without introducing future-phase ideas.
6. Update `PHASE_CHECKLIST.md` to add a P2·C7 entry and mark it complete so the tracker matches the new work.

## Block C checkpoints
- run `pytest -q`, `ruff check .`, and `black --check .` until all pass.
- confirm `docs/phase-roadmap.md`/`docs/Phase_Roadmap.md` or other ops entry points link to the new baseline and debrief artifacts so operators can easily find them.
- prepare the final summary noting the changed files, UI run instructions (manual and systemd), baseline steps, and debrief highlights without speculating beyond Phase 2.
