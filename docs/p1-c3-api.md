# Prompt Valet Phase 1·C3 API

## Running the control plane
1. Install dependencies from the manifest:
   ```
   python3 -m pip install -U pip
   python3 -m pip install -e ".[dev]"
   ```
   `python-multipart` is part of the base requirements now, so this command already makes multipart uploads available in runtime installs.
2. Start the service from the repo root:
   ```
   python3 scripts/pv_api.py
   ```
3. Override defaults with environment variables if needed (`PV_REPOS_ROOT`, `PV_RUNS_ROOT`, `PV_STALL_THRESHOLD_SECONDS`, `PV_BIND_HOST`, `PV_BIND_PORT`). The app also reads `/srv/prompt-valet/config/prompt-valet.yaml` for `git_owner`/`inbox_mode` metadata.

## Upload reminders
- `POST /api/v1/jobs/upload` accepts any `.md` file (the filename is only checked for the `.md` suffix); non-`.md` parts are rejected.
- Uploaded prompts are rewritten to include the generated `job_id` and to end with `.prompt.md`, so the existing watcher still recognizes them.
- Because we now prefer `.prompt.md` filenames for the API-written files, operators always see job-id-aware `.prompt.md` artifacts in the inbox and processed trees.

## Sample requests
- Health: `curl http://127.0.0.1:8888/api/v1/healthz`
- Discovery: `curl http://127.0.0.1:8888/api/v1/targets`
- Jobs: `curl "http://127.0.0.1:8888/api/v1/jobs?state=running&limit=5"`
