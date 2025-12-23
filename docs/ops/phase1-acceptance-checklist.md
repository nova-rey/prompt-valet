# Phase 1 · Checkpoint 6 Acceptance Checklist

Use this numbered checklist to record the day/operator who verified each action. Match the expected observable outcome before checking the box.

1. Start services (watcher + API).  
   - Expected outcome: `prompt-valet-watcher.service` is `active (running)` and `prompt-valet-api.service` moves to `active (running)` with no repeated crash loop.  
   - Date / operator: ______________________

2. Submit a job through the API (POST `/api/v1/jobs` or upload).  
   - Expected outcome: API replies `201 Created`, `.prompt.md` hits the inbox, and `runs/<job_id>/job.json` is created.  
   - Date / operator: ______________________

3. Observe job state transitions.  
   - Expected outcome: `GET /api/v1/jobs/{job_id}` follows `created` → `running` → terminal (`succeeded`/`failed`/`aborted`).  
   - Date / operator: ______________________

4. Stream or tail job logs.  
   - Expected outcome: `/api/v1/jobs/{job_id}/log/stream` produces SSE lines while the job is running, and `/srv/prompt-valet/runs/{job_id}/job.log` fills with the same content.  
   - Date / operator: ______________________

5. Abort the running job via `/api/v1/jobs/{job_id}/abort`.  
   - Expected outcome: API acknowledges the abort, the watcher sees `runs/{job_id}/ABORT`, and the job becomes `aborted` in both API and filesystem metadata.  
   - Date / operator: ______________________

6. Verify terminal state and cleanup.  
   - Expected outcome: Processed prompt lands in `/srv/prompt-valet/finished`, job metadata shows the final state, and `/api/v1/jobs?stalled=true` reports zero (unless there is a genuine stall).  
   - Date / operator: ______________________
