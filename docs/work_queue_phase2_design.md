# Prompt Valet — Work Queue Integration (Q2 Design)

This document defines the wiring, lifecycle, and observability expectations for the queue-enabled path without touching execution logic. Q2 is the scaffold: it establishes the intake/executor contracts, retry rules, inbox handling policies, logging schema, and compatibility surface so that Q3 can implement deterministic, testable runners without reworking the existing pipeline.

## 1. Intake Stage (Watcher → Queue)

1. The watcher stops spawning Codex runs and becomes a prompt detector + job author.
2. Upon seeing `inbox/<repo>/<branch>/<timestamp>_<slug>.md`, it extracts the canonical metadata set: `repo`, `branch`, `slug`, `timestamp`, caller identity, inbox path, and derived identifiers such as the eventual `job_id`.
3. A job entry is materialized under `<jobs_root>/<job_id>/` (jobs_root stays configurable via existing config). Inside each job directory lives structured state data:
   * `state: queued`
   * `repo`, `branch`, `inbox_file` (absolute path), `slug`
   * `created_at`/`updated_at` (UTC ISO 8601)
   * `retries: 0`
   * `source_timestamp` (matching inbox file name)
   * `metadata` bucket for future extensions (e.g., trigger_source, priority)
4. The watcher emits a `job.created` log with the job fields above, the queue path, and the incoming file path.
5. Movement: Q2 leaves inbox files untouched; movement rules are documented later. The watcher reports `job.requeued` only in later phases.

## 2. Execution Stage (Queue → Executor)

1. Introduce `prompt-valet-executor.service`, a long-running loop responsible for draining the queue.
2. The executor looks for jobs whose directory state marker is `queued`, with eligibility determined by job age (oldest first) and optionally future metadata filters (e.g., priority).
3. Once selected, the executor atomically updates the job state to `running`, records `started_at`, and emits `job.running` with job context.
4. The loop then hands control over to Q3 work (Codex runner etc.) but Q2 insists the executor never assumes success or failure until the actual work reports back.
5. After work concludes, the executor updates the job to `done`, `failed`, or higher-level summaries such as `failed_final`, always emitting the corresponding log event.
6. Between polls, the executor sleeps briefly (e.g., one second) when no eligible jobs exist to avoid tight spin loops.

## 3. Retry Logic

1. A central configuration value `max_retries` governs how many times a job may transition from `failed` back into `queued`.
2. Failure outcomes must declare a category (`retryable`, `fatal`, `transient`, etc.) so retriers know whether to requeue. Q2 specifies the semantics, while Q3 defines how categories are derived.
3. Retry eligibility:
   * `retries < max_retries`
   * failure category is explicitly retryable
   * optional rate-limiting or backoff metadata can be stored alongside the job for Q3 to honor
4. Transition pattern: `failed` → `queued` when incomplete but eligible; increment `retries`, emit `job.requeued`, and leave other fields (e.g., `failure_reason`) for observability. Fatal failures go to `failed_final` and stay there.

## 4. Inbox File Movement Policy

1. During Q2 we do not move files; however, we document the future policies:
   * **Running:** inbox artefact remains where it was so watchers/executors can re-read or requeue it safely.
   * **Success:** move to `processed/<repo>/<branch>/<job_id>/` and record `processed_path` inside the job to keep the provenance link.
   * **Retryable Failure:** leave the inbox file untouched so retried runs see the same content.
   * **Fatal Failure:** allow config to choose between leaving the file or moving it to `failed/<repo>/<branch>/<job_id>/` for later inspection; the job records `archived_path` when movement occurs.
2. These policies ensure that Q3 can move files atomically after verifying the job outcome without changing watcher expectations beforehand.

## 5. Logging / Observability Schema

Q2 defines a minimal event vocabulary so that logs can be used for dashboards and alerts. Each event is a structured log line (e.g., JSON or key-value) and must include `job_id`, `state`, `repo`, `branch`, and `reason` when applicable.

Required events:
   * `job.created` – emitted once when the watcher enqueues the job.
   * `job.running` – when executor claims the job; includes `owner` (executor id) and `started_at`.
   * `job.succeeded` – on clean completion; includes `duration`, `processed_path`.
   * `job.failed.retryable` – when a retryable failure occurs; includes `failure_reason`, `retries`, `next_retry_at` (optional).
   * `job.failed.final` – when retries are exhausted or failure is fatal; includes `failure_reason`.
   * `job.requeued` – emitted when a job is explicitly requeued (including retry transitions); include `retries`.
   * `job.archived` – when inbox files or job data are moved aside; include `archived_path`.

These events also feed into health dashboards and sprawled instrumentation layers in future phases.

## 6. Backward Compatibility

Introduce `queue.enabled: true|false` in the shared configuration. Behavior:
   * `false` (default for existing installs): watcher continues to launch Codex directly, ignoring the queue subsystem entirely. The jobs directory can exist but is never touched by watcher/executor.
   * `true`: watcher stops direct execution, jobs are enqueued, and the executor service drains them. The queue remains the single source of truth for job lifecycle.

Switching modes is deterministic:
   * Enabling the queue requires starting or restarting `prompt-valet-executor.service`; the watcher simply flips to enqueue mode without altering inbox files.
   * Disabling the queue keeps the previous work flow untouched; leftover job directories can be ignored or cleaned manually.
   * If the queue is disabled mid-run, the executor will drain gracefully (marking running jobs as failed_final if they cannot complete). Q3 will handle transitions, but Q2 ensures the flags exist so mode toggles do not create dual writers.

## Diagram

```
[ watcher ] 
     ↓ detects/new prompt
[ queue (job directories with state) ]
     ↓ polled by
[ executor ]
     ↓ runs Q3 logic (Codex runner, reporters)
[ PR creation + job finalization ]
     ↓
[ processed/ or failed/ ]
```

## Summary

Q2 establishes the queue-based control surface: the watcher now mirrors prompt metadata into jobs, the executor loops over queued work, retries become first-class, inbox movement rules are documented for later implementation, logs carry a strict schema, and compatibility is maintained via the `queue.enabled` flag. Q3 can now focus on deterministic execution against this scaffolding without worrying about watcher or Codex regressions.
