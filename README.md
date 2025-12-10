# prompt-valet

Prompt Valet now supports a queue-aware watcher/executor flow when `queue.enabled`
is configured in `config/prompt-valet.yaml`. The new `scripts/queue_runtime.py`
module stores job metadata under `<pv_root>/.queue/jobs`, and the executor loop in
`scripts/codex_watcher.py` drains `queued` work into the existing Codex runner,
with retry and failure archival controls under `queue.max_retries` and
`queue.failure_archive`.
