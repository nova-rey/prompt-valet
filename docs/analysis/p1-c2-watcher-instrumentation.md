# Phase 1 · Checkpoint 2 Watcher Instrumentation

## Deep scan results
- **Watcher entry point:** `scripts/codex_watcher.py` is launched via `systemd/prompt-valet-watcher.service` (see `install_prompt_valet.sh` and the service unit) and exposes `main()` which claims inbox prompts (`claim_new_prompts`) and batches `.running.md` files (`start_jobs_from_running`).
- **Job claiming:** A prompt is claimed by renaming `<inbox>/**/*.prompt.md` → `.running.md` in `claim_new_prompts`, then copied into `processed/<owner>/<repo>/<branch>/<run_id>/prompt.md` before handing a `Job` instance to the worker queue (see `start_jobs_from_running`).
- **Subprocess spawning:** `run_codex_for_job()` builds the `codex exec` command, calls `subprocess.run()`, captures its stdout/stderr, and raises if the CLI exits with a nonzero code. That call is the only place Codex executes, so Watcher is the parent process of every job subprocess.
- **Stdout/stderr today:** `subprocess.run(capture_output=True)` captures both streams and routes them through the watcher logger (`log()`), so per-job stdout/stderr are mixed into the watcher logs rather than being persisted alongside the job.
- **Job start/exit:** The single start moment is when `run_codex_for_job()` is invoked inside `run_prompt_job()` after the run directory and prompt copy exist; the single exit moment is when `subprocess.run()` returns (successfully, with an exception, or after an external abort signal).

## Lifecycle timeline
1. **Queued (new prompt):** `claim_new_prompts()` atomically renames a `*.prompt.md` file to `*.running.md`, enqueues it (or creates a `Job` directly when queue mode is disabled), and assigns a deterministic `job_id` that matches the future job directory naming scheme.
2. **Running (execution):** The watcher creates `<runs_root>/<job_id>/` (using `Path(CONFIG["runs"])`), writes the initial `job.json`, touches `job.log`, starts the heartbeat/abort monitor, launches Codex (`subprocess.Popen()` so the PID is observable), and records the child PID + heartbeat timestamp. If the directory already exists (e.g., rerun), we remove any stale `ABORT` marker before execution so the watch loop only reacts to fresh requests.
3. **Terminal (finished/failed/aborted):** When the Codex subprocess exits (or is terminated by an abort marker), Watcher atomically updates `job.json` with `state`, `exit_code`, timestamps, and leaves the `runs/<job_id>/` directory intact for auditing; the job is also archived to `processed/`/`finished/` by the existing `finalize_inbox_prompt()` once the filesystem contract runs.

## Instrumentation insertion points
1. **Job ID extraction/generation:**
   - Queue mode already owns a UUID job ID from `queue_runtime.JobRecord.job_id`.
   - Direct runs already mint a `run_id` (`YYYYMMDD-HHMM-SS`) before creating the `Job`; instrumentation reuses that value via `job.job_id` so the metadata, log, and final run paths stay in sync.
2. **`runs/<job_id>/` creation:**
   - At job start (just before invoking Codex), create `<pv_root>/runs/<job_id>/` so all instrumentation artifacts share a consistent base (location derived from `CONFIG["pv_root"]`).
3. **Initial `job.json`:**
   - Still inside `run_prompt_job()`, write `job.json` describing cache fields: `state=running`, `started_at`, `pid` (once the subprocess is launched), `log_path`, `inbox_*`, and `heartbeat_at`. `log_path` points at `runs/<job_id>/job.log`, which is created/zeroed before execution and later populated with Codex `stdout`/`stderr`. Use a temporary file + `os.replace()` to guarantee atomicity.
4. **PID capture:**
   - Launch Codex via `subprocess.Popen` so the PID is available immediately, then persist `proc.pid` to `job.json` for diagnostic and abort tooling consumers.
5. **Heartbeat update loop:**
   - Run a dedicated thread (or timer) that wakes every 5 seconds, refreshes `heartbeat_at` inside `job.json`, and checks `<runs>/<job_id>/ABORT` for abort requests. Each heartbeat write uses atomic rename semantics so `job.json` is always well-formed.
6. **Terminal state finalization:**
   - When the Codex process exits (success, failure, or abort), stop the heartbeat thread, capture `exit_code`/status, record a companion `finished_at`, set `state` to `succeeded|failed|aborted`, and re-persist `job.json` atomically.

## Heartbeat semantics
- **Where written from:** A per-job heartbeat thread within `run_prompt_job()` updates `job.json` for as long as the child process is alive. It also serves as the polling loop for abort marker detection.
- **Interval:** 5 seconds (configurable by a constant) balances responsiveness with IO cost; it matches the existing `POLL_INTERVAL_SECONDS` for other watcher loops.
- **Atomic writes guarantee:** Heartbeats call a helper that dumps the JSON payload to `job.json.tmp` and `os.replace()`s it into place, ensuring readers never see partially written metadata (same approach as `queue_runtime._atomic_write_json()`).
- **Watcher shutdown:** On watcher termination (`stop_event`), the heartbeat thread receives a signal and stops updating; any running job will naturally terminate once the parent stops (subprocess is killed), so heartbeats simply cease and `heartbeat_at` freezes. Observers can detect state staleness by comparing `heartbeat_at` with the current time.

## Abort semantics (chosen handshake: abort marker file)
- **Mechanism:** Future tooling will request aborts by creating `<pv_root>/runs/<job_id>/ABORT`. The heartbeat thread checks for this file on every cycle. If the file exists the watcher:
  1. Kills the Codex subprocess (sending `SIGTERM` first, then `SIGKILL` if needed).
  2. Sets `state = aborted`, records the signal-derived `exit_code`, and refreshes timestamps inside `job.json` before marking the job terminal.
  3. Leaves the `ABORT` marker in place so clients can confirm the request was honored.
- **Why marker file:** The filesystem is already the contract surface for this project; a marker file keeps the handshake observable (no additional API), avoids making Watcher expose PIDs externally, and easily survives a watcher restart. A PID-only handshake would not tell Watcher whether a signal came from user intent versus an unrelated system action.

## Explicit non-goals
- No FastAPI/HTTP code or new network APIs.
- No refactor of watcher execution logic, scheduling, or concurrency beyond the necessary instrumentation.
- No retries, requeue policy changes, or new job selection criteria.
- No UI work; the instrumentation is filesystem-only.
- No schema changes to `schemas/job_record.schema.json` (we may augment `job.json` with additional properties but do not alter the published schema file).
- No altering of the existing job claim/finalize semantics beyond what is required for bookkeeping.
