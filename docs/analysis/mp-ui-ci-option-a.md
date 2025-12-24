# MP-UI-CI Option A Contract Harness

## Block A findings

| Endpoint | Method | Client request payload | Expected response shape | Status codes | Notes |
| --- | --- | --- | --- | --- | --- |
| `/api/v1/targets` | GET | none | `[{"repo": str, "branch": str, "inbox_path": str, "owner": str | None, "full_repo": str}]` | 200 | UI needs `repo`/`branch` pairs, `owner` optional when single-owner inbox. |
| `/api/v1/jobs` | GET | optional query params (`state`, `repo`, `branch`, `stalled`, `limit`) | `{ "jobs": [<job payload dict> ...] }` where each payload includes `job_id`, metadata from `JobRecord`, plus `stalled`/`age_seconds` numbers | 200 | Client currently only reads the `jobs` list. |
| `/api/v1/jobs/{job_id}` | GET | none | `<job payload dict>` with the same metadata as above | 200 / 404 | Used to populate detail view. 404 when job missing. |
| `/api/v1/jobs` | POST | JSON: `repo`, `branch`, `markdown_text`, optional `filename` | `{ "job_id": str, "inbox_path": str, "created_at": ISO-8601 }` | 201 / 400 / 404 / 500 | Client expects 201 success body; we only need happy-path in stub. |
| `/api/v1/jobs/upload` | POST | form fields `repo`, `branch` + `files` (multipart UploadFile list) | `{ "jobs": [{"job_id": str, "inbox_path": str, "created_at": ISO-8601}, ...] }` | 201 / 400 | Stub will accept any `.md`-style filenames and respond deterministically. |
| `/api/v1/jobs/{job_id}/log` | GET | optional query `lines` (int) | plaintext log tail (`lines` most recent entries) | 200 / 404 | Client uses text body; stub can return pre-seeded log string. |
| `/api/v1/jobs/{job_id}/log/stream` | GET | none | SSE compositing `data: <line>` entries, `\n\n` delimited stream | 200 / 404 | Client only yields stripped data lines; stub can send fixed messages then close. |
| `/api/v1/jobs/{job_id}/abort` | POST | none | `{ "job_id": str, "previous_state": str, "abort_requested_at": ISO-8601 }` | 200 / 409 / 404 | Client must handle success vs 409 when job not running. Stub will enforce running-only abort semantics. |


### Deterministic testing approach

- FastAPI app in `tests/fixtures/stub_api.py` will declare in-memory `TARGETS` and `JOBS` dictionaries plus logs to answer these endpoints without touching disk.
- SSE endpoint will stream a fixed list of `data:` lines and terminate (no async sleeping) so the `PromptValetAPIClient.stream_job_log` iterator can be exercised directly.
- Tests will use `httpx.ASGITransport(app=stub_app)` so the client talks to the stub in-process; this keeps everything synchronous/fast and avoids binding sockets.
- The stub will expose mutate-able state for job runs so abort can succeed only when the job is marked `running`; otherwise it raises HTTP 409.

## Next steps

1. Implement the FastAPI stub and expose the endpoints above.
2. Add an integration test (`tests/test_ui_api_contract.py`) that wires `PromptValetAPIClient` to the stub and verifies each helper method, including SSE parsing and abort error handling.
3. Ensure `docs/analysis/mp-ui-ci-option-a.md` (this file) is referenced during the Mini-Push to capture the analysis requirements.
