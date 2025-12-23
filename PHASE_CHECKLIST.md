# Phase Checklist

- P1·C1 – Job contract documented and enforced (schema + queue helper updates).
- P1·C2 – Watcher now tracks runs/<job_id> metadata, job.log, heartbeats, and abort markers.
- P1·C3 – FastAPI control plane exposes health/status, TreeBuilder discovery, and read-only job queries.
- P1·C4 – FastAPI now accepts job submissions/upload, merges `pv` frontmatter, and atomically drops `.prompt.md` files into inboxes.
- P1·C5 – Added log tail + stream endpoints and abort controls that mirror runs/<job_id> artifacts with the filesystem handshake.
