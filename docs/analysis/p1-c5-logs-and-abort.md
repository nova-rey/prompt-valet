# Phase 1 · Checkpoint 5 Log Access & Abort Controls

## Endpoint semantics

- `GET /api/v1/jobs/{job_id}/log`
  - Read `runs/<job_id>/job.json` for the `log_path` that Watcher created (see `scripts/codex_watcher.py` ≥ 1320). If the job directory or metadata is missing, return `404`.
  - Default to returning the last 200 lines (`lines` query param, `ge=1`) and only read the requested suffix so large logs do not fill memory.
  - The response is plain text (not JSON) so that tooling can mimic `tail`.

- `GET /api/v1/jobs/{job_id}/log/stream`
  - Establish an SSE (`text/event-stream`) generator that tails `job.log` line-by-line, starting from the beginning and emitting every appended line as `data: …`.
  - Refresh the job’s `state` (`job.json`) on every loop to know when the watcher has marked it terminal, and stop the stream immediately once `state` is not `"running"`.
  - Handle missing job/log as `404` before starting the stream; if the client disconnects (`asyncio.CancelledError`), the generator exits cleanly without mutating any filesystem state.

- `POST /api/v1/jobs/{job_id}/abort`
  - Verify the job directory exists and that `state` (from `job.json`) is still a non-terminal value (only `"running"` is considered in-flight today because instrumentation writes `state=running` at job start and switches to `"succeeded"`, `"failed"`, or `"aborted"` on completion).
  - Touch `runs/<job_id>/ABORT` atomically (write to `<path>.tmp` + `os.replace`) so the watcher’s heartbeat thread can pick it up; the API never updates `job.json` or the state file.
  - Return `{ "job_id": ..., "previous_state": ..., "abort_requested_at": <ISO8601 UTC> }`. If the job is already terminal, respond `409 Conflict` (with a clear message) rather than mutating the filesystem.


## Log access algorithm

1. Load `job.json` to confirm the job exists and to fetch the `log_path` that Watcher wrote during instrumentation.
2. Check that the log file exists; if it is missing, emit `404 Log not found`.
3. Tail the file without reading it all: seek from the end in 4 KiB blocks, counting newline bytes until at least `lines` entries are available (or the file start is reached). Decode the accumulated bytes via `utf-8`/`replace` and return the final slice joined with `\n`.
4. Requests with invalid `lines` (e.g., 0 or negative) should fail fast with `400`.

Safe defaults keep the transfer small (200-line tail) while still honoring larger `lines` values from operators. This avoids buffering multi-megabyte logs in memory while keeping the log history accessible.

## Stream lifecycle

- Use a FastAPI `StreamingResponse` with `media_type="text/event-stream"` and an `async` generator that:
  1. Opens `job.log` with `encoding="utf-8"`/`errors="replace"` and initially seeks to the file start.
  2. Loops indefinitely:
     - Reads new lines via `readline()`; for every returned line, strip trailing newline characters, wrap it as `data: …`, and yield an SSE event terminated by `\n\n`.
     - After exhausting available data, check `job.json` again (via `get_job_record`) to see if `state.lower() != "running"`. If so, break to end the stream.
     - Sleep `await asyncio.sleep(0.5)` before the next poll so we avoid busy-looping and give the watcher time to write more output or finalize the job.
  3. Handles client disconnects by allowing `asyncio.CancelledError` to bubble out, ending the generator without touching log files or state.

Streaming stops automatically when the watcher’s heartbeat/finalization thread switches `state` away from `"running"` (for example to `"succeeded"`, `"failed"`, or `"aborted"`) even if the log file continues to grow afterward.

## Abort handshake semantics

- The watcher already honors an `ABORT` marker (`runs/<job_id>/ABORT`) checked every heartbeat loop (see `_start_job_heartbeat` in `scripts/codex_watcher.py` around line 1029). When the file is detected, the watcher terminates the Codex subprocess, sets `state="aborted"`, and leaves the marker in place.
- The API enforces idempotency: if the marker already exists, the endpoint still returns success with the same `abort_requested_at` semantics because the watcher only cares that the file exists, not how many times it was written.
- Atomic creation is implemented via a temp file + `os.replace`, so the watcher never sees a partially written marker.
- Concurrent abort calls simply rediscover the same `ABORT` file and return the prior state; no extra job state transitions happen in the API layer, and the watcher retains sole authority for the final `state`.
- If the job is terminal (`state` not `"running"`), the API returns `409 Conflict` with a message like “Job already terminal (state=X)” and does not touch the filesystem.

## Explicit non-goals

1. No scheduler, retry, requeue, or watcher-refactor logic happens here; the filesystem contract is still the only coordination surface.
2. Do not mutate `job.json`, `state`, or the watcher’s heartbeat loop—only read `state` to detect terminal conditions.
3. There is no authentication, database, or process-killing logic in the API (the watcher remains the authoritative owner of running processes and abort handling).
4. These endpoints only expose read access and abort intent; they do not reorder jobs, change watcher configuration, or introduce new `/jobs` mutations beyond abort markers.
