# Phase 1 · Prompt Valet Operator Guide

## Install dependencies
Ensure the Control Plane can satisfy FastAPI and its helpers. From the Prompt Valet repo root run:
```
pip install -e ".[dev]"
```
This installs FastAPI, uvicorn, python-multipart, and the development/test dependencies that the API expects.

## Deploy the API service
1. Copy the environment template into place and tailor the settings before starting the API:
   ```bash
   sudo mkdir -p /etc/prompt-valet
   sudo cp ops/env/pv.env.example /etc/prompt-valet/pv.env
   sudo chown root:root /etc/prompt-valet/pv.env
   sudo chmod 644 /etc/prompt-valet/pv.env
   ```
   Edit `/etc/prompt-valet/pv.env` to point `PV_REPOS_ROOT`, `PV_RUNS_ROOT`, `PV_BIND_HOST`, `PV_BIND_PORT`, and `PV_STALL_THRESHOLD_SECONDS` at the desired paths/ports. Leave `PV_API_USER` at `prompt-valet` (or replace it with an existing user) so the service drops privileges via `runuser`.
2. Install the systemd unit:
   ```bash
   sudo cp ops/systemd/prompt-valet-api.service /etc/systemd/system/
   sudo systemctl daemon-reload
   ```
   The service is intentionally not enabled by default. Start it when you are ready for manual testing:
   ```bash
   sudo systemctl start prompt-valet-api.service
   sudo systemctl status prompt-valet-api.service
   ```
3. Tail the journal while the service starts:
   ```bash
   sudo journalctl -u prompt-valet-api.service -f
   ```

## Health check
Ensure the API is reachable on the configured bind address (adjust the host/port if you customized `PV_BIND_HOST`/`PV_BIND_PORT`):
```
curl -s http://127.0.0.1:8888/api/v1/healthz
```
Expected output is a small JSON document such as `{"status":"ok","version":"0.0.0"}`.

## Submit a job
Send a markdown payload that the watcher will later claim from the inbox:
```
cat <<'JSON' | curl -s -o /tmp/pv-job.json -w '\n' -X POST http://127.0.0.1:8888/api/v1/jobs \
  -H "Content-Type: application/json" \
  -d @-
{
  "repo": "nova-rey/example",
  "branch": "main",
  "markdown_text": "# Demo run\nAdd or modify files as requested.",
  "filename": "demo.prompt.md"
}
JSON
```
`curl` returns `201 Created`. Inspect `/tmp/pv-job.json` (or the raw response) for `job_id`, `inbox_path`, and timestamps. The API drops a `.prompt.md` file into the configured inbox so the watcher sees it within seconds.

## List jobs and observe state
```
curl -s http://127.0.0.1:8888/api/v1/jobs | jq .
curl -s http://127.0.0.1:8888/api/v1/jobs/{job_id} | jq .
curl -s http://127.0.0.1:8888/api/v1/status | jq .
```
Replace `{job_id}` with the ID returned during submission. Expect the per-job record to show `state` transitions (`created` → `running` → terminal). `/api/v1/status` reports `jobs.counts` and `jobs.stalled_running`, making it easy to confirm the API and watcher share the same roots.

## Stream logs
Tail the job log or follow the Server-Sent Events stream:
```
curl -s http://127.0.0.1:8888/api/v1/jobs/{job_id}/log | tail
curl -N http://127.0.0.1:8888/api/v1/jobs/{job_id}/log/stream
```
The SSE stream emits `data:` events until the job leaves the running state. You can also inspect `/srv/prompt-valet/runs/{job_id}/job.log` to see the same output.

## Abort a job
If the job is still running, request an abort:
```
curl -X POST http://127.0.0.1:8888/api/v1/jobs/{job_id}/abort
```
The response includes `previous_state` and `abort_requested_at`. The watcher detects the `runs/{job_id}/ABORT` file and stops Codex, marking the job as `aborted` in `runs/{job_id}/job.json`.

## Diagnose stalled jobs
Stalled runs have not updated `heartbeat_at` for `PV_STALL_THRESHOLD_SECONDS`. The API surfaces them directly:
```
curl -s http://127.0.0.1:8888/api/v1/jobs?stalled=true | jq .
curl -s http://127.0.0.1:8888/api/v1/status | jq . | grep stalled_running
```
If the API reports `stalled_running > 0`, inspect `/srv/prompt-valet/runs/{job_id}/job.json` and the log to see how old the last heartbeat is. Clearing a stale `ABORT` marker or restarting `prompt-valet-watcher.service` often resolves the stall.

## UI service
The NiceGUI-based UI mirrors the FastAPI control plane without touching job files or the watcher tree. All UI HTTP calls flow through the API base URL, so the UI can be treated as a status/submit dashboard layer on top of the Phase 1 endpoints.

### Environment template
The UI service has its own environment template. Copy it alongside the API template before installing the service:
```bash
sudo mkdir -p /etc/prompt-valet
sudo cp ops/env/pv-ui.env.example /etc/prompt-valet/pv-ui.env
sudo chown root:root /etc/prompt-valet/pv-ui.env
sudo chmod 644 /etc/prompt-valet/pv-ui.env
```
Edit `/etc/prompt-valet/pv-ui.env` to customize `PV_API_BASE_URL`, `PV_UI_BIND_HOST`, `PV_UI_BIND_PORT`, and `PV_UI_API_TIMEOUT_SECONDS`. Leave `PV_UI_USER` set to `prompt-valet` (or point it at an existing user) so the service drops privileges via `runuser`.

### Manual execution
Install the NiceGUI and HTTPX extras via `pip install -e ".[dev]"`, then run the UI directly when you want to test without systemd:
```bash
PV_API_BASE_URL=http://127.0.0.1:8888/api/v1 \
PV_UI_BIND_HOST=0.0.0.0 \
PV_UI_BIND_PORT=8080 \
PV_UI_API_TIMEOUT_SECONDS=5.0 \
./scripts/pv_ui.py
```
The header indicator polls `/api/v1/healthz` and flashes green (reachable) or red (unreachable). When the UI shows “API reachable” you know the NiceGUI service can talk to the API. Open `http://127.0.0.1:8080/` to see the Dashboard/Submit/Services tabs and interact with the UI console, while the API continues to serve jobs at `/api/v1`.

### Systemd service
Copy the new unit into place, reload systemd, and start the UI when you need always-on visibility:
```bash
sudo cp ops/systemd/prompt-valet-ui.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start prompt-valet-ui.service
sudo systemctl status prompt-valet-ui.service
```
To enable auto-start on boot:
```bash
sudo systemctl enable prompt-valet-ui.service
```

### Connectivity checks
Check the health indicator inside the UI to confirm UI↔API connectivity. If the header stays red you can curl the API health endpoint to diagnose the backend:
```
curl -s http://127.0.0.1:8888/api/v1/healthz
```
Normal output looks like `{"status":"ok","version":"0.0.0"}`. The UI waits up to `PV_UI_API_TIMEOUT_SECONDS` seconds for each ping, so you can extend that value via the env template if the API runs on a slow host.

### Troubleshooting
Tail the service journal to surface NiceGUI or network errors:
```bash
sudo journalctl -u prompt-valet-ui.service -f
```
If the UI cannot reach the API, review `/etc/prompt-valet/pv-ui.env` for `PV_API_BASE_URL` typos and confirm the API is reachable on the bind host/port. The UI is intentionally read-only: if the UI fails or the service stops, the API, watcher, and job execution remain unaffected.
