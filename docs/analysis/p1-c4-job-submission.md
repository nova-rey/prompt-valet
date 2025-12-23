# Phase 1 · Checkpoint 4 Job Submission Analysis

## Deep scan & reference surfaces
- FastAPI already lives under `prompt_valet/api/app.py`; `create_app` wires `/api/v1` routers (targets, jobs, status, health), so the new submission endpoints must plug into that existing router rather than introducing a second app instance.
- Target discovery is encapsulated in `prompt_valet/api/discovery.py`: `list_targets(settings)` walks `settings.tree_builder_root`, prefers `.pv_inbox` markers, and infers `(owner, repo, branch)` tuples for both legacy single-owner and `multi_owner` layouts. The returned `InboxTarget.inbox_path` is the directory we must write into.
- Existing job metadata processing lives under `prompt_valet/api/jobs.py`, while the watcher/queue lives under `scripts/codex_watcher.py`/`scripts/queue_runtime.py` and already assumes `job_id` is `uuid.uuid4().hex` (see `queue_runtime.enqueue_job` and `pv_jobs.Job.from_inbox_path`). We must match that `uuid4` pattern and keep the runs tree untouched.
- The watcher only reacts to `*.prompt.md` files (see `scripts/codex_watcher.py::_statusified_name`, `claim_inbox_prompt`, and the `for path in inbox_root.rglob("*.prompt.md")` loop), so our generated filenames should still end in `.prompt.md` while meeting the new job_id requirement.
- The repository already defines Markdown jobs as `.prompt.md` in docs/analysis and tests (e.g., `tests/test_p1_c3_api.py`, `tests/test_inbox_lifecycle.py`), underscoring the need to keep compatible naming and not disrupt watcher behavior.

## Job submission semantics
- `POST /jobs` will accept JSON `{repo, branch, filename?, markdown_text}`. We must:
  * resolve `{repo, branch}` via `list_targets(settings)` and require exactly one match; fail (400) when missing/ambiguous.
  * derive the inbox directory from `InboxTarget.inbox_path` and reject missing or non-writable paths (status 500/409 as appropriate).
  * require `markdown_text` to be a non-empty string; reject otherwise.
  * choose a filename that includes the generated UUIDv4 `job_id`, ends with `.md`, and still matches `*.prompt.md` so the watcher will eventually see it (e.g., `prefix-<job_id>.prompt.md` or `<job_id>.prompt.md`).
  * write the file atomically: render the final Markdown, merge/insert frontmatter (see below), write to `target_path.tmp`, then `os.replace` to the real file.
  * respond with `{job_id, inbox_path, created_at}` using ISO-8601 `created_at`.
- `POST /jobs/upload` will mirror the same validation but accept multipart `.md` files:
  * reject any part whose filename doesn't end with `.md`.
  * read the bytes/text safely, parse/merge frontmatter per the same rules, and stamp each file with a new job_id (even if the incoming file already contained a pv job_id).
  * treat each upload as a separate job and return a list of responses; fail the entire request if any file fails validation before writing.

## Job identity & frontmatter contract
- We follow the existing `uuid.uuid4().hex` convention so the filesystem and queue downstream see familiar 32-character lowercase IDs.
- Filenames must embed the job_id and retain `.md` extension (ideally `.prompt.md` suffix to keep watcher compatibility). Any existing prefix or timestamp is allowed, but the token `<job_id>` must appear once so humans and tools can trace it.
- Every Markdown must begin with YAML frontmatter `---`/`---`. We parse with `yaml.safe_load`/`yaml.safe_dump` to avoid arbitrary code execution. When a file already contains frontmatter, we retain its keys, only ensuring there is a `pv` section with the authoritative fields:
  ```
  pv:
    job_id: "<uuid>"
    repo: "<repo>"
    branch: "<branch>"
    created_at: "<iso>"
    source: "api"
  ```
  If the file already had a `pv` block, we merge the new fields without removing other keys (e.g., preserve `pv.notes` if present). The `pv` block should appear immediately after the opening `---`, but we do not reorder non-`pv` sections.
- For files missing frontmatter, we insert the entire block at the top followed by a blank line before the Markdown body.
- We preserve the original Markdown body verbatim, just swapping or injecting frontmatter; no other content should shift.

## Failure modes & guardrails
- **Repo/branch validation:** Respond 400 when `(repo, branch)` is absent, duplicates the same inbox, or the configured tree builder root cannot be read (per `list_targets` behavior).
- **Inbox accessibility:** If the resolved inbox path does not exist or is not writable, fail with a descriptive 500/409 so callers can retry after operators restore permissions.
- **Invalid Markdown payload:** Reject requests where `markdown_text` is empty/non-string or when uploaded part contents cannot be decoded as UTF-8.
- **Filename collisions:** If the computed target path already exists (which could happen if the request is retried after a success or another API call raced in), fail with 409 unless we can bump to a fresh job_id/filename; we will avoid collisions by generating a new UUID for each attempt and re-checking before writing.
- **Invalid uploads:** `POST /jobs/upload` must reject any attachment whose filename lacks the `.md` suffix (415 or 400) and reject the whole request rather than writing partial data.
- **No mutations elsewhere:** The API must never write `runs/*/job.json` or touch the watcher queue artifacts; submission is purely in the inbox directory.

## File write algorithm & permissions
- Compose the final Markdown string with the merged frontmatter and append a newline if necessary. We use a temporary path (e.g., `target_path.with_suffix(target_path.suffix + ".tmp")` or `target_path.with_name(target_path.name + ".tmp")`) in the same directory before calling `os.replace` so the write is atomic and the watcher never sees partial content (same pattern as `scripts/codex_watcher._atomic_write_text`).
- Ensure the parent inbox directory exists (`mkdir(parents=True, exist_ok=True)`) before writing; trust the TreeBuilder to manage owners, but document that the API inherits the directory permissions from whatever created the inbox.
- After `os.replace`, the file should live under the target name with the proper frontmatter. The created path is the one returned via `inbox_path`.
- We will not change ownership or chmod; rely on the existing tree’s defaults. The API runs under the same PV user as the watcher, so the file will be readable/writable by the watcher process.

## Explicit non-goals (hard guardrails)
- No abort, log tail, or stream endpoints.
- No requeue/retry logic or mutation of `runs/*/job.json`.
- The watcher behavior (claiming `.prompt.md` files, `queue_runtime`, job states) must remain untouched; the submission API merely plants Markdown in a recognized inbox path.
- No schema changes to existing job metadata; frontmatter is limited to the `pv` block described above.
- No discovery refactors—reuse `list_targets` and the current config surface (`APISettings`/`tree_builder_root`).

This analysis completes Block A; the next step is to implement the described endpoints, frontmatter handling, atomic writes, and accompanying tests (Block B).
