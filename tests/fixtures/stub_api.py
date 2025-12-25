from __future__ import annotations

from collections import Counter
import itertools
from datetime import datetime, timezone
from typing import AsyncIterator, Dict, List, Sequence, Tuple

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel


def _iso_now() -> str:
    return datetime.utcnow().replace(microsecond=0, tzinfo=timezone.utc).isoformat()


STREAM_JOB_ID = "job-streaming"
COMPLETED_JOB_ID = "job-complete"
STREAM_LOG_TEXT = "stream log line 1\nstream log line 2\n"
STREAM_SSE_LINES = ["stream entry 1", "stream entry 2"]
COMPLETED_LOG_TEXT = "complete line\n"
COMPLETED_SSE_LINES = ["complete entry"]


class StubTarget:
    def __init__(self, repo: str, branch: str, inbox_path: str, owner: str | None):
        self.repo = repo
        self.branch = branch
        self.inbox_path = inbox_path
        self.owner = owner

    def to_dict(self) -> Dict[str, str | None]:
        return {
            "repo": self.repo,
            "branch": self.branch,
            "inbox_path": self.inbox_path,
            "owner": self.owner,
            "full_repo": f"{self.owner}/{self.repo}" if self.owner else self.repo,
        }


class JobSubmissionPayload(BaseModel):
    repo: str
    branch: str
    markdown_text: str
    filename: str | None = None


class StubState:
    def __init__(self) -> None:
        self.targets: List[StubTarget] = [
            StubTarget("repo-one", "main", "/inbox/main", "acme"),
            StubTarget("repo-two", "dev", "/inbox/dev", None),
        ]
        self.jobs: Dict[str, Dict[str, object]] = {}
        self.logs: Dict[str, str] = {}
        self.streams: Dict[str, List[str]] = {}
        self._counter = itertools.count(1)
        self._seed(
            STREAM_JOB_ID,
            repo="acme/repo-one",
            branch="main",
            job_state="running",
            log_lines=STREAM_LOG_TEXT.strip().splitlines(),
            stream_lines=STREAM_SSE_LINES,
        )
        self._seed(
            COMPLETED_JOB_ID,
            repo="other/repo",
            branch="preview",
            job_state="succeeded",
            log_lines=COMPLETED_LOG_TEXT.strip().splitlines(),
            stream_lines=COMPLETED_SSE_LINES,
        )

    def _seed(
        self,
        job_id: str,
        *,
        repo: str,
        branch: str,
        job_state: str,
        log_lines: Sequence[str],
        stream_lines: Sequence[str],
    ) -> None:
        entry = {
            "job_id": job_id,
            "repo": repo,
            "branch": branch,
            "state": job_state,
            "created_at": _iso_now(),
            "updated_at": _iso_now(),
            "heartbeat_at": _iso_now() if job_state == "running" else None,
            "stalled": False,
            "age_seconds": 0.0,
        }
        self.jobs[job_id] = entry
        self.logs[job_id] = "\n".join(log_lines) + ("\n" if log_lines else "")
        self.streams[job_id] = list(stream_lines)

    def register_submission(
        self, repo: str, branch: str, filename: str | None
    ) -> Tuple[Dict[str, object], str]:
        job_id = f"submit-{next(self._counter)}"
        entry = {
            "job_id": job_id,
            "repo": repo,
            "branch": branch,
            "state": "running",
            "created_at": _iso_now(),
            "updated_at": _iso_now(),
            "heartbeat_at": _iso_now(),
            "stalled": False,
            "age_seconds": 0.0,
            "submitted_filename": filename,
        }
        self.jobs[job_id] = entry
        self.logs[job_id] = f"submitted log for {job_id}\n"
        self.streams[job_id] = [f"submitted stream {job_id}"]
        inbox = f"/stubs/{job_id}.prompt.md"
        return entry, inbox

    def register_uploads(
        self, repo: str, branch: str, filenames: Sequence[str]
    ) -> List[Dict[str, str]]:
        results: List[Dict[str, str]] = []
        for filename in filenames:
            job_id = f"upload-{next(self._counter)}"
            self.jobs[job_id] = {
                "job_id": job_id,
                "repo": repo,
                "branch": branch,
                "state": "running",
                "created_at": _iso_now(),
                "updated_at": _iso_now(),
                "heartbeat_at": _iso_now(),
                "stalled": False,
                "age_seconds": 0.0,
                "submitted_filename": filename,
            }
            self.logs[job_id] = f"uploaded {filename} for {job_id}\n"
            self.streams[job_id] = [f"upload stream {filename}"]
            results.append(
                {
                    "job_id": job_id,
                    "inbox_path": f"/stubs/{job_id}.prompt.md",
                    "created_at": _iso_now(),
                }
            )
        return results


def create_stub_app() -> FastAPI:
    state = StubState()
    app = FastAPI(title="Prompt Valet UI stub")
    router = APIRouter(prefix="/api/v1")

    @router.get("/healthz")
    def healthz() -> Dict[str, str]:
        return {"status": "ok", "version": "stub"}

    @router.get("/status")
    def status() -> Dict[str, object]:
        counts = Counter(job.get("state") for job in state.jobs.values())
        total_jobs = len(state.jobs)
        stalled_running = sum(
            1
            for job in state.jobs.values()
            if job.get("state") == "running" and job.get("stalled")
        )
        return {
            "status": "ok",
            "config": {
                "runs_root": "/tmp/runs",
                "tree_builder_root": "/tmp/tree",
                "stall_threshold_seconds": 60,
                "bind_host": "127.0.0.1",
                "bind_port": 8000,
            },
            "jobs": {
                "counts": dict(counts),
                "total": total_jobs,
                "stalled_running": stalled_running,
            },
            "targets": {"count": len(state.targets)},
            "roots": {
                "runs_root_exists": True,
                "tree_builder_root_exists": True,
            },
        }

    @router.get("/targets")
    def list_targets() -> List[Dict[str, str | None]]:
        return [target.to_dict() for target in state.targets]

    @router.get("/jobs")
    def list_jobs() -> Dict[str, List[Dict[str, object]]]:
        return {"jobs": list(state.jobs.values())}

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str) -> Dict[str, object]:
        job = state.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return job

    @router.get("/jobs/{job_id}/log")
    def job_log(job_id: str, lines: int = Query(200, ge=1)) -> PlainTextResponse:
        log = state.logs.get(job_id)
        if log is None:
            raise HTTPException(status_code=404, detail="Log missing")
        clipped_lines = log.strip().splitlines()[-lines:]
        clipped = "\n".join(clipped_lines)
        if clipped:
            clipped += "\n"
        return PlainTextResponse(content=clipped, media_type="text/plain")

    @router.get("/jobs/{job_id}/log/stream")
    def job_log_stream(job_id: str) -> StreamingResponse:
        if job_id not in state.jobs:
            raise HTTPException(status_code=404, detail="Job not found")

        async def stream() -> AsyncIterator[str]:
            for message in state.streams.get(job_id, []):
                yield f"data: {message}\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    @router.post("/jobs/{job_id}/abort")
    def abort_job(job_id: str) -> Dict[str, str]:
        job = state.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        previous_state = job["state"]
        if previous_state != "running":
            raise HTTPException(
                status_code=409,
                detail=f"Job already terminal (state={previous_state})",
            )
        job["state"] = "aborted"
        return {
            "job_id": job_id,
            "previous_state": previous_state,
            "abort_requested_at": _iso_now(),
        }

    @router.post("/jobs", status_code=201)
    def submit_job(payload: JobSubmissionPayload) -> Dict[str, str]:
        submission, inbox = state.register_submission(
            payload.repo, payload.branch, payload.filename
        )
        return {
            "job_id": submission["job_id"],
            "inbox_path": inbox,
            "created_at": submission["created_at"],
        }

    @router.post("/jobs/upload", status_code=201)
    async def upload_jobs(
        repo: str = Form(...),
        branch: str = Form(...),
        files: List[UploadFile] = File(...),
    ) -> Dict[str, List[Dict[str, str]]]:
        if not files:
            raise HTTPException(status_code=400, detail="No files provided")
        filenames = [upload.filename or "unnamed.md" for upload in files]
        for upload in files:
            await upload.read()
        return {"jobs": state.register_uploads(repo, branch, filenames)}

    app.include_router(router)
    return app
