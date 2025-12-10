# Prompt Valet Job Queue — Overview (Q1 Skeleton)

This document describes the initial job model and filesystem layout introduced in the Q1 mini-push. At this stage, the queue is **not yet wired into the watcher flow**; it exists as a standalone building block for future phases.

## Goals

- Represent each unit of work (a Codex run for a given prompt) as a structured JSON **job**.
- Store jobs on disk in a predictable, inspectable tree.
- Provide a small Python helper module for creating, listing, and updating jobs.
- Avoid changing existing watcher behavior until the queue is ready to become the canonical path.

## Directory Layout

All jobs live under a configurable root (to be chosen by the caller in later phases), with one subdirectory per status:

```text
<jobs_root>/
  pending/
  running/
  finished/
  failed/
  superseded/
```

Each job is stored as a single JSON file named {job_id}.json inside the directory that matches its current status.

The JSON schema is intentionally simple and forward-compatible; it captures both the “who/what” and “where in the lifecycle” for each job.

## Job Schema (Q1)

The scripts/pv_jobs.py module defines a Job dataclass with the following fields:

- job_id — Unique identifier for the job (UUID hex).
- repo — Repository name (e.g., Screaming-Penguin, CrapsSim-Control).
- branch — Branch name being targeted for the run.
- logical_prompt — Human-facing prompt identifier (e.g., P12.prompt.md).
- prompt_path — Inbox path for the prompt relative to the inbox root (e.g., Screaming-Penguin/main/P12.prompt.md).
- prompt_sha256 — Content hash for the prompt body.
- base_commit — Optional git commit hash used as the base for the run.
- status — One of: pending, running, finished, failed, superseded.
- attempt — Integer attempt counter for reruns.
- rerun_of — Optional job_id of the original job when this is a rerun.
- superseded_by — Optional job_id that superseded this job.
- created_at — Creation timestamp in UTC (ISO 8601 with Z).
- updated_at — Last update timestamp in UTC.
- metadata — Free-form dictionary for future extensions.

## Public API (Q1)

The following helpers are exposed by scripts/pv_jobs.py:

- ensure_jobs_root(root)

- Ensures the jobs root and all status subdirectories exist. Returns the root path.
- create_job(root, *, repo, branch, logical_prompt, prompt_path, prompt_sha256, base_commit=None, attempt=1, rerun_of=None, metadata=None)

- Creates a new pending job, writes it to disk, and returns the Job.
- list_jobs(root, status=None)

- Lists jobs in the given status bucket. When status is None, all buckets are scanned.
- find_job_by_id(root, job_id)

- Searches all buckets for the given job_id. Returns a Job or None.
- mark_job_status(root, job_id, new_status, extra_fields=None)

- Loads the job, updates its status and any provided fields, moves the JSON file to the corresponding status directory, and returns the updated Job.

## Behavior Guarantees (Q1)

- The job store is append-only and move-only: jobs are written once and then moved between status buckets.
- JSON writes are done via a write-then-rename pattern to reduce the risk of partial files.
- Listing jobs ignores malformed JSON entries instead of crashing the process.
- No existing watcher behavior is changed in this checkpoint; nothing in the main Prompt Valet pipeline depends on the job store yet.

## Implementation status

- `scripts/queue_runtime.py` implements the Q1 job runtime, keeping job directories under `<pv_root>/.queue/jobs/<job_id>/`.
- The Q2 watcher/executor wiring is now in `scripts/codex_watcher.py` via `queue.enabled`, while `run_prompt_job` still runs Codex for both modes (Q3 scope).
- The `queue.max_retries`/`queue.failure_archive` settings and the new tests in `tests/test_queue_runtime.py` and `tests/test_queue_watcher.py` validate the lifecycle from `job.created` through `job.failed.final`.
