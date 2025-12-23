# Prompt Valet Phase 1·C3 API

## Running the control plane
1. Ensure the environment has FastAPI and Uvicorn (`python3 -m pip install fastapi uvicorn`).
2. Start the service from the repo root:
   ```
   python3 scripts/pv_api.py
   ```
3. Override defaults with environment variables if needed (`PV_REPOS_ROOT`, `PV_RUNS_ROOT`, `PV_STALL_THRESHOLD_SECONDS`, `PV_BIND_HOST`, `PV_BIND_PORT`). The app also reads `/srv/prompt-valet/config/prompt-valet.yaml` for `git_owner`/`inbox_mode` metadata.

## Sample requests
- Health: `curl http://127.0.0.1:8888/api/v1/healthz`
- Discovery: `curl http://127.0.0.1:8888/api/v1/targets`
- Jobs: `curl "http://127.0.0.1:8888/api/v1/jobs?state=running&limit=5"`
