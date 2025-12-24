# Phase 2 Baseline Procedure

## Happy-path steps
1. Ensure the API, watcher, and tree builder are running. The API (`prompt-valet-api.service`) serves `/api/v1`, the watcher is watching `/srv/prompt-valet/runs`, and the tree builder feeds targets. If you need to start the UI, follow the manual command or systemd steps in the operator guide.
2. Start the UI service (`prompt-valet-ui.service`) or run `./scripts/pv_ui.py` with `PV_API_BASE_URL` pointing at the API before you submit anything so you can observe status in real time.
3. Submit a simple markdown job via the UI Submit tab or the API (`curl -X POST http://127.0.0.1:8888/api/v1/jobs ...`). Use the Dashboard tab to confirm the job appears, watch the heartbeats, and look for the state transition into `running`/terminal.
4. Tail the log stream from the job in either the UI log panel or directly with `curl -N http://127.0.0.1:8888/api/v1/jobs/{job_id}/log/stream` and `tail /srv/prompt-valet/runs/{job_id}/job.log` so you can correlate UI output and filesystem traces.
5. Exercise the abort flow by hitting `POST /api/v1/jobs/{job_id}/abort` (UI or `curl`). Confirm the UI reflects the `aborted` state and the watcher sets the `runs/{job_id}/ABORT` token while the API returns the previous state.

## What to verify
- Submission: a markdown job drafts in the UI and the API returns `job_id`, `inbox_path`, and success payload.
- Dashboard: the new UI dashboard refreshes job counts, state badges, and the watcher/target cards show data after manual refresh or the periodic poll.
- Logs: the Services tab and log stream reflect activity, and `/srv/prompt-valet/runs/{job_id}/job.log` contains the same output.
- Abort: the API accepts the abort request, the job toggles to `aborted`, and the UI highlights the change.

## Notes
- The UI polls `/api/v1/healthz` to display connectivity, but if the UI itself fails or is not running the API/watchers will still continue job processing uninterrupted. Treat the UI as a non-critical observability layer while Phase 2 focuses on the installer, systemd services, and documentation.
