from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

JOB_STATUS_PENDING = "pending"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_FINISHED = "finished"
JOB_STATUS_FAILED = "failed"
JOB_STATUS_SUPERSEDED = "superseded"

VALID_STATUSES = {
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    JOB_STATUS_FINISHED,
    JOB_STATUS_FAILED,
    JOB_STATUS_SUPERSEDED,
}


def _utc_now_iso() -> str:
    """Return a simple UTC timestamp without microseconds in ISO 8601 form."""
    return _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


@dataclass
class Job:
    """
    A single unit of work for Prompt Valet.

    This is intentionally generic and only stores metadata, not execution details.
    """

    job_id: str
    repo: str
    branch: str
    logical_prompt: str
    prompt_path: str
    prompt_sha256: str
    base_commit: Optional[str] = None

    status: str = JOB_STATUS_PENDING
    attempt: int = 1

    rerun_of: Optional[str] = None
    superseded_by: Optional[str] = None

    created_at: str = dataclasses.field(default_factory=_utc_now_iso)
    updated_at: str = dataclasses.field(default_factory=_utc_now_iso)

    # Free-form metadata bag for future extensions (e.g., codex config, tags).
    metadata: Dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "repo": self.repo,
            "branch": self.branch,
            "logical_prompt": self.logical_prompt,
            "prompt_path": self.prompt_path,
            "prompt_sha256": self.prompt_sha256,
            "base_commit": self.base_commit,
            "status": self.status,
            "attempt": self.attempt,
            "rerun_of": self.rerun_of,
            "superseded_by": self.superseded_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Job":
        return cls(
            job_id=data["job_id"],
            repo=data["repo"],
            branch=data["branch"],
            logical_prompt=data["logical_prompt"],
            prompt_path=data["prompt_path"],
            prompt_sha256=data["prompt_sha256"],
            base_commit=data.get("base_commit"),
            status=data.get("status", JOB_STATUS_PENDING),
            attempt=int(data.get("attempt", 1)),
            rerun_of=data.get("rerun_of"),
            superseded_by=data.get("superseded_by"),
            created_at=data.get("created_at", _utc_now_iso()),
            updated_at=data.get("updated_at", _utc_now_iso()),
            metadata=dict(data.get("metadata", {})),
        )


def ensure_jobs_root(root: Path | str) -> Path:
    """
    Ensure the jobs root and status subdirectories exist.

    Layout:

        <root>/
          pending/
          running/
          finished/
          failed/
          superseded/
    """
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)

    for status in VALID_STATUSES:
        (root_path / status).mkdir(parents=True, exist_ok=True)

    return root_path


def _status_dir(root: Path, status: str) -> Path:
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status '{status}'")
    return root / status


def _job_path(root: Path, status: str, job_id: str) -> Path:
    return _status_dir(root, status) / f"{job_id}.json"


def _load_job_file(path: Path) -> Job:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    job = Job.from_dict(data)
    # Trust the JSON for status, not the directory name.
    return job


def _save_job_file(path: Path, job: Job) -> None:
    tmp_path = path.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(job.to_dict(), f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp_path, path)


def create_job(
    jobs_root: Path | str,
    *,
    repo: str,
    branch: str,
    logical_prompt: str,
    prompt_path: str,
    prompt_sha256: str,
    base_commit: Optional[str] = None,
    attempt: int = 1,
    rerun_of: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Job:
    """
    Create a new pending job and persist it to disk.

    The caller is responsible for deriving logical_prompt / prompt_path semantics.
    """
    root = ensure_jobs_root(Path(jobs_root))
    job_id = uuid.uuid4().hex

    job = Job(
        job_id=job_id,
        repo=repo,
        branch=branch,
        logical_prompt=logical_prompt,
        prompt_path=prompt_path,
        prompt_sha256=prompt_sha256,
        base_commit=base_commit,
        status=JOB_STATUS_PENDING,
        attempt=attempt,
        rerun_of=rerun_of,
        superseded_by=None,
        metadata=metadata or {},
    )
    job.updated_at = _utc_now_iso()

    path = _job_path(root, job.status, job.job_id)
    _save_job_file(path, job)
    return job


def list_jobs(
    jobs_root: Path | str,
    status: Optional[str] = None,
) -> List[Job]:
    """
    List jobs from the jobs directory.

    If status is provided, only that status bucket is scanned.
    Otherwise, all buckets are scanned.
    """
    root = ensure_jobs_root(Path(jobs_root))
    statuses: Iterable[str]
    if status is None:
        statuses = VALID_STATUSES
    else:
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status '{status}'")
        statuses = (status,)

    results: List[Job] = []
    for st in statuses:
        bucket = _status_dir(root, st)
        if not bucket.exists():
            continue
        for entry in bucket.iterdir():
            if not entry.is_file() or not entry.name.endswith(".json"):
                continue
            try:
                job = _load_job_file(entry)
                results.append(job)
            except Exception:
                # Corrupt/malformed job files should not crash listing; they can be
                # inspected or repaired separately.
                continue

    # Sort by created_at then job_id for stable ordering.
    results.sort(key=lambda j: (j.created_at, j.job_id))
    return results


def find_job_by_id(
    jobs_root: Path | str,
    job_id: str,
) -> Optional[Job]:
    """
    Locate a job by job_id across all status buckets.

    Returns None if not found.
    """
    root = ensure_jobs_root(Path(jobs_root))
    for status in VALID_STATUSES:
        candidate = _job_path(root, status, job_id)
        if candidate.exists():
            return _load_job_file(candidate)
    return None


def mark_job_status(
    jobs_root: Path | str,
    job_id: str,
    new_status: str,
    extra_fields: Optional[Dict[str, Any]] = None,
) -> Job:
    """
    Transition a job to a new status and move its file accordingly.

    Raises FileNotFoundError if the job does not exist.
    Raises ValueError if the new status is invalid.
    """
    if new_status not in VALID_STATUSES:
        raise ValueError(f"Invalid status '{new_status}'")

    root = ensure_jobs_root(Path(jobs_root))

    # Find the job file in any bucket.
    current_path: Optional[Path] = None
    current_status: Optional[str] = None
    for status in VALID_STATUSES:
        candidate = _job_path(root, status, job_id)
        if candidate.exists():
            current_path = candidate
            current_status = status
            break

    if current_path is None or current_status is None:
        raise FileNotFoundError(f"Job '{job_id}' not found")

    job = _load_job_file(current_path)

    job.status = new_status
    job.updated_at = _utc_now_iso()

    if extra_fields:
        # Only update known top-level attributes or metadata.
        for key, value in extra_fields.items():
            if hasattr(job, key):
                setattr(job, key, value)
            else:
                job.metadata[key] = value

    # Persist to the new bucket then remove the old file.
    new_path = _job_path(root, new_status, job.job_id)
    _save_job_file(new_path, job)
    if new_path != current_path and current_path.exists():
        current_path.unlink()

    return job
