# Phase 1 · Checkpoint 1 Job Contract

## Deep scan & assumption validation
- **Scope:** inspected `docs/Filesystem.md`, `docs/operations.md`, `docs/Architecture.md`, `docs/job_queue_overview.md`, `config/prompt-valet.yaml`, `scripts/codex_watcher.py`, `scripts/queue_runtime.py`, and related tests/descriptions to understand current inbox/repo/run artifacts.
- **Filesystem coordination is primary:** the watcher claims jobs by renaming `*.prompt.md` into `*.running.md`, copies them into `processed/.../<run_id>/prompt.md`, and finally moves status files into `finished/` (see `start_jobs_from_running`, `claim_inbox_prompt`, `finalize_inbox_prompt` in `scripts/codex_watcher.py`). No external database appears anywhere; every piece of state lives on disk.
- **Watcher owns execution:** the long-running `codex_watcher.py` orchestrates claim → run → finalize (with optional queue-mode executor threads) and keeps a volatile `JOB_STATES` map to prevent dupes even while multiple threads operate.
- **No database-backed state:** job metadata is mirrored from the inbox into disk files under `<pv_root>/.queue/jobs/` (see `queue_runtime.enqueue_job`, `job_record.job_dir`, and `JobRecord.from_disk`).
- **Concurrent jobs are supported:** `queue_enabled` turns on executor threads that pull from the queue while the watcher still processes new `.prompt.md` files; retries, failure archives, and `JOB_STATES` keep per-job context isolated.

## Job state machine
- **States:** `queued`, `running`, `succeeded`, `failed_retryable`, `failed_final`.
- **Allowed transitions:**
  - `queued` → `running` when the executor starts a job.
  - `running` → `succeeded` on success, or → `failed_retryable`/`failed_final` on failure.
  - `failed_retryable` → `queued` when re-queued, or → `failed_final` if retries are exhausted.
  - `succeeded` and `failed_final` are terminal (no outgoing transitions).
- Invalid transitions are bugs and should raise/log so operators can surface momentum or reorder.

## Job schema & `job.json`
The canonical metadata file for a job (stored as `job.json` under each job directory) exposes the following fields. Refer to `schemas/job_record.schema.json` for the machine-verifiable definition.

| Field | Type | Required? | Notes |
| --- | --- | --- | --- |
| `job_id` | string | yes | 32 lowercase hex chars (UUIDv4 without hyphens, matches directory name). |
| `git_owner` / `repo_name` / `branch_name` | string | yes | Derived from the inbox path and config; `branch_name` is the target branch for PR creation. |
| `inbox_file` | string | yes | Absolute path to the claimed `*.running.md` file. |
| `inbox_rel` | string | yes | Path relative to the configured inbox root. Used for deduplication (`JOB_STATES`) and for archive paths. |
| `state` | string | yes | One of the five canonical states. Stored both here and in the sibling `state` file for quick scans. |
| `retries` | integer | yes | Non-negative counter, incremented only when `requeue()` runs. |
| `created_at` / `updated_at` | string | yes | ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`). `created_at` is set once; `updated_at` refreshes on every write. |
| `metadata` | object | yes | Free-form bag; `reason`, `last_failure`, and `last_retry` are used today. |
| `processed_path` | string  null | no | Absolute path to the archived prompt under `processed/` once the run finishes. |
| `failure_reason` | string  null | no | Text explaining a fatal failure. Stored only when marking a job as failed. |
| `archived_path` | string  null | no | Path inside `failed/` when `failure_archive` is enabled and a prompt is moved aside. |

Derived fields such as `stalled` (e.g., `state == running` && `now - updated_at > threshold`) are computed at runtime and intentionally omitted from `job.json`. All stored fields must survive restarts.

## Job directory layout
- **Jobs root:** default is `<pv_root>/.queue/jobs/`, overridable via `queue.jobs_root` in the YAML config (see `codex_watcher._queue_root_from_config`).
- **Per-job directory:** `<jobs_root>/<job_id>/` contains exactly two artifacts:
  1. `job.json` — the metadata defined above.
  2. `state` — plain-text file whose contents are the current state value; kept in sync with `job.json` for fast scanners.
- **Ownership:** the watcher/executor owns both files and updates them through the helpers in `scripts/queue_runtime.py`. Executors must never touch a job directory owned by another thread.
- **Run artifacts:** while a job executes, a working directory is created at `<processed_root>/<git_owner>/<repo_name>/<branch_name>/<job_id>/`. It contains:
  - `prompt.md` — a faithful copy of the claimed prompt file.
  - `NO_INPUT.md` — created only when the prompt copy is missing, documenting the edge case.
  - Runner outputs (Codex writes under `docs/AGENT_RUNS/` inside the cloned repo, not inside `processed/`).

After completion, `finalize_inbox_prompt` moves `.running.md` → `.done.md` or `.error.md` in the inbox, waits a few seconds, and then mirrors the file structure under `finished/`. Failures may also copy prompts into the configured `failed/` tree when `queue.failure_archive` is true.

## Atomic read/write rules
- Job metadata writes go through a `write temp → rename` cycle to avoid partial files; `queue_runtime._write_job()` mirrors this pattern by dumping JSON to `job.json.tmp` and `os.replace`.
- The `state` file now follows the same pattern: `_write_state()` writes to `state.tmp` and renames so readers never see truncated or interleaved bytes.
- Every `_persist_job()` call updates `updated_at` before writing both files, guaranteeing `job.json` and `state` agree on the current state and timestamp.
- Readers load `job.json` first and validate it against the schema (see `queue_runtime._validate_job_payload`). If the file is missing, malformed, or fails schema checks, the job is skipped and logged.

## Invariants
1. `job_id` is a 32-character lowercase hex string and matches both the directory name and the `job_id` field inside `job.json`.
2. `state` in `job.json` and the `state` file are identical and one of the five canonical values.
3. `created_at` ≤ `updated_at`; timestamps reset on each `_persist_job()` call.
4. `retries` only increments when transitioning through `failed_retryable` → `queued` (see `_requeue()`).
5. `processed_path` is populated only after a successful run; `failure_reason`/`archived_path` are written only in failure branches.
6. `queue_runtime.validate_job_payload` rejects metadata lacking required strings or otherwise invalid types, preventing corrupt jobs from entering the queue.
7. `JOB_STATES` deduplication keys (`inbox_rel`) continue to mirror `job.json` so we never process the same prompt twice concurrently.
8. Run directories mirror the inbox path and `job_id` so manual inspection can correlate a prompt in `finished/` with its queue record.

## Non-goals for P1·C1
1. No changes to the watcher/runtime behavior beyond this contract (we only formalize what already exists).
2. No database or external state engine is introduced; the filesystem remains the only source of truth.
3. The `codex` CLI invocation, PR creation flow, and repo cloning logic stay untouched.
4. No UI, API, heartbeat, or scheduler work outside the contract artifact.

## Work artifacts
- JSON schema: `schemas/job_record.schema.json` describes `job.json`.
- Core helpers: `scripts/queue_runtime.py` enforces transitions, atomic writes, and schema validation.
- Documentation: this file captures the state machine, schema explanation, invariants, and non-goals required for the checkpoint.
