"""FastAPI control plane for Prompt Valet."""

from __future__ import annotations

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
from pydantic import BaseModel

from prompt_valet import __version__
from prompt_valet.api.config import APISettings, get_api_settings
from prompt_valet.api.discovery import list_targets
from prompt_valet.api.jobs import (
    filter_jobs,
    get_job_record,
    list_job_records,
)
from prompt_valet.api.submissions import submit_job, submit_job_from_upload


class JobSubmissionPayload(BaseModel):
    repo: str
    branch: str
    markdown_text: str
    filename: str | None = None


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
