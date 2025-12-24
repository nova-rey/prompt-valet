# Phase Checklist

- P1·C1 – Job contract documented and enforced (schema + queue helper updates).
- P1·C2 – Watcher now tracks runs/<job_id> metadata, job.log, heartbeats, and abort markers.
- P1·C3 – FastAPI control plane exposes health/status, TreeBuilder discovery, and read-only job queries.
- P1·C4 – FastAPI now accepts job submissions/upload, merges `pv` frontmatter, and atomically drops `.prompt.md` files into inboxes.
- P1·C5 – Added log tail + stream endpoints and abort controls that mirror runs/<job_id> artifacts with the filesystem handshake.
- P1·C6 – Added API hardening artifacts (service unit, env template, operator + acceptance docs) while keeping runtime behavior unchanged.
- P2·C1 – NiceGUI UI service skeleton + health wiring, with the new CLI entrypoint, API client wrapper, and header connectivity indicator against `/api/v1/healthz`.
- P2·C2 – Jobs dashboard wired to `/api/v1/jobs` and detail panel via `/api/v1/jobs/{job_id}`, surfacing read-only metadata, heartbeat/stall indicators, and placeholder links for later actions.
- P2·C3 – Submit tab now supports Compose + Upload flows through `/api/v1/jobs` and `/api/v1/jobs/upload`, including inbox target selectors, markdown/file inputs, and per-job success feedback.
- P2·C4 – Job detail dialog now includes recent log tail, live SSE streaming, pause controls, and typed abort confirmation powered by the existing Phase 1 endpoints.
- P2·C5 – Services tab surfaces Watcher and TreeBuilder visibility (status, heartbeat, targets) plus a manual refresh/error path using only the Phase 1 APIs.
- P2·C6 – Hardened NiceGUI for mobile: responsive dashboard/detail controls, stacked submit/services flows, and more robust log streaming UX without touching the backend.
- P2·C7 – Ops packaging, docs, and baseline for the NiceGUI UI service (env/template, systemd unit, operator guide, baseline, and debrief artifacts).
