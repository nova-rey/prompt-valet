"""FastAPI control plane for Prompt Valet."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

from collections import Counter

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from prompt_valet import __version__
from prompt_valet.api.config import APISettings, get_api_settings
from prompt_valet.api.discovery import list_targets
from prompt_valet.api.jobs import (
    JobRecord,
    filter_jobs,
    get_job_record,
    list_job_records,
)
from prompt_valet.api.submissions import submit_job, submit_job_from_upload

DEFAULT_LOG_LINES = 200
LOG_TAIL_CHUNK_SIZE = 4096
STREAM_POLL_INTERVAL_SECONDS = 0.5
TERMINAL_STATES = {"succeeded", "failed", "aborted"}


class JobSubmissionPayload(BaseModel):
    repo: str
    branch: str
    markdown_text: str
    filename: str | None = None


def _now_utc_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _is_terminal_state(state: str | None) -> bool:
    if not state:
        return False
    return state.lower() in TERMINAL_STATES


def _resolve_job_and_log(job_id: str, settings: APISettings) -> tuple[JobRecord, Path]:
    record = get_job_record(
        job_id, settings.runs_root, settings.stall_threshold_seconds
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Job not found")
    log_path_value = record.payload.get("log_path")
    log_path = (
        Path(log_path_value)
        if log_path_value
        else settings.runs_root / job_id / "job.log"
    )
    return record, log_path


def _tail_file(path: Path, lines: int) -> str:
    if lines <= 0:
        return ""
    with path.open("rb") as fp:
        fp.seek(0, os.SEEK_END)
        buffer = bytearray()
        newline_count = 0
        position = fp.tell()
        while position > 0 and newline_count <= lines:
            read_size = min(LOG_TAIL_CHUNK_SIZE, position)
            position -= read_size
            fp.seek(position)
            chunk = fp.read(read_size)
            newline_count += chunk.count(b"\n")
            buffer[:0] = chunk
            if position == 0:
                break
        text = buffer.decode("utf-8", errors="replace")
    lines_with_endings = text.splitlines(keepends=True)
    if not lines_with_endings:
        return ""
    selected = lines_with_endings[-lines:]
    return "".join(selected)


async def _stream_job_log_generator(
    job_id: str, settings: APISettings, log_path: Path
) -> AsyncIterator[str]:
    with log_path.open("r", encoding="utf-8", errors="replace") as fp:
        fp.seek(0)
        while True:
            line = fp.readline()
            if line:
                payload = line.rstrip("\r\n")
                yield f"data: {payload}\n\n"
                continue
            record = get_job_record(
                job_id, settings.runs_root, settings.stall_threshold_seconds
            )
            if record is None or _is_terminal_state(record.state_lower):
                break
            await asyncio.sleep(STREAM_POLL_INTERVAL_SECONDS)


def create_app(settings: APISettings | None = None) -> FastAPI:
    app = FastAPI(title="Prompt Valet Control Plane", version=__version__)
    router = APIRouter(prefix="/api/v1")

    def _settings() -> APISettings:
        return settings or get_api_settings()

    @router.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @router.get("/status")
    def status(settings: APISettings = Depends(_settings)) -> dict[str, object]:
        jobs = list_job_records(settings.runs_root, settings.stall_threshold_seconds)
        targets = list_targets(settings)
        counts = Counter(job.state_lower for job in jobs)
        stalled_running = sum(
            1 for job in jobs if job.state_lower == "running" and job.stalled
        )
        roots = {
            "tree_builder_root": str(settings.tree_builder_root),
            "runs_root": str(settings.runs_root),
        }
        return {
            "status": "ok",
            "config": {
                **roots,
                "stall_threshold_seconds": settings.stall_threshold_seconds,
                "bind_host": settings.bind_host,
                "bind_port": settings.bind_port,
            },
            "jobs": {
                "counts": dict(counts),
                "total": len(jobs),
                "stalled_running": stalled_running,
            },
            "targets": {"count": len(targets)},
            "roots": {
                "tree_builder_root_exists": settings.tree_builder_root.is_dir(),
                "runs_root_exists": settings.runs_root.is_dir(),
            },
        }

    @router.get("/targets")
    def targets(
        settings: APISettings = Depends(_settings),
    ) -> list[dict[str, str | None]]:
        discovered = list_targets(settings)
        return [target.to_dict() for target in discovered]

    @router.get("/jobs")
    def jobs(
        state: str | None = Query(None),
        repo: str | None = Query(None),
        branch: str | None = Query(None),
        stalled: bool | None = Query(None),
        limit: int | None = Query(None, ge=1),
        settings: APISettings = Depends(_settings),
    ) -> dict[str, list[dict[str, object]]]:
        records = list_job_records(settings.runs_root, settings.stall_threshold_seconds)
        filtered = filter_jobs(
            records, state=state, repo=repo, branch=branch, stalled=stalled
        )
        if limit is not None:
            filtered = filtered[:limit]
        return {"jobs": [record.to_dict() for record in filtered]}

    @router.get("/jobs/{job_id}")
    def job_detail(
        job_id: str, settings: APISettings = Depends(_settings)
    ) -> dict[str, object]:
        record = get_job_record(
            job_id, settings.runs_root, settings.stall_threshold_seconds
        )
        if record is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return record.to_dict()

    @router.get("/jobs/{job_id}/log")
    def job_log(
        job_id: str,
        lines: int = Query(DEFAULT_LOG_LINES, ge=1),
        settings: APISettings = Depends(_settings),
    ) -> PlainTextResponse:
        _, log_path = _resolve_job_and_log(job_id, settings)
        if not log_path.is_file():
            raise HTTPException(status_code=404, detail="Job log not found")
        payload = _tail_file(log_path, lines)
        return PlainTextResponse(content=payload, media_type="text/plain")

    @router.get("/jobs/{job_id}/log/stream")
    def job_log_stream(job_id: str, settings: APISettings = Depends(_settings)):
        _, log_path = _resolve_job_and_log(job_id, settings)
        if not log_path.is_file():
            raise HTTPException(status_code=404, detail="Job log not found")
        generator = _stream_job_log_generator(job_id, settings, log_path)
        return StreamingResponse(generator, media_type="text/event-stream")

    @router.post("/jobs/{job_id}/abort")
    def abort_job(
        job_id: str, settings: APISettings = Depends(_settings)
    ) -> dict[str, str]:
        record, _ = _resolve_job_and_log(job_id, settings)
        if not record.state_lower == "running":
            raise HTTPException(
                status_code=409,
                detail=f"Job already terminal (state={record.state})",
            )
        abort_path = settings.runs_root / job_id / "ABORT"
        if not abort_path.exists():
            temp = abort_path.with_suffix(".tmp")
            temp.write_text(_now_utc_iso(), encoding="utf-8")
            os.replace(temp, abort_path)
        return {
            "job_id": job_id,
            "previous_state": record.state or "",
            "abort_requested_at": _now_utc_iso(),
        }

    @router.post("/jobs", status_code=201)
    def submit_job_endpoint(
        payload: JobSubmissionPayload,
        settings: APISettings = Depends(_settings),
    ) -> dict[str, str]:
        return submit_job(
            settings,
            payload.repo,
            payload.branch,
            payload.markdown_text,
            filename=payload.filename,
        )

    @router.post("/jobs/upload", status_code=201)
    async def submit_upload(
        repo: str = Form(...),
        branch: str = Form(...),
        files: list[UploadFile] = File(...),
        settings: APISettings = Depends(_settings),
    ) -> dict[str, list[dict[str, str]]]:
        if not files:
            raise HTTPException(
                status_code=400, detail="At least one Markdown file must be uploaded."
            )
        responses: list[dict[str, str]] = []
        for upload in files:
            filename = upload.filename
            if not filename or not filename.lower().endswith(".md"):
                raise HTTPException(
                    status_code=400,
                    detail="Uploaded files must have a '.md' extension.",
                )
            try:
                raw = await upload.read()
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise HTTPException(
                    status_code=400,
                    detail="Uploaded files must be UTF-8 encoded.",
                )
            responses.append(
                submit_job_from_upload(settings, repo, branch, filename, text)
            )
        return {"jobs": responses}

    app.include_router(router)
    return app


app = create_app()
