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
