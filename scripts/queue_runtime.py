from __future__ import annotations

import datetime as dt
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

STATE_FILE = "state"
META_FILE = "meta.json"

STATE_QUEUED = "queued"
STATE_RUNNING = "running"
STATE_SUCCEEDED = "succeeded"
STATE_FAILED_RETRYABLE = "failed_retryable"
STATE_FAILED_FINAL = "failed_final"

VALID_STATES = {
    STATE_QUEUED,
    STATE_RUNNING,
    STATE_SUCCEEDED,
    STATE_FAILED_RETRYABLE,
    STATE_FAILED_FINAL,
}


def _utc_iso_now() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


logger = logging.getLogger("prompt_valet.queue")


def ensure_jobs_root(root: Path | str) -> Path:
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _job_dir(root: Path, job_id: str) -> Path:
    return root / job_id


def _write_state(path: Path, state: str) -> None:
    path.write_text(state, encoding="utf-8")


def _load_state(path: Path) -> Optional[str]:
    if not path.exists():
        logger.debug("Skipping job %s without state file", path)
        return None
    return path.read_text(encoding="utf-8").strip()


def _write_meta(path: Path, meta: Dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fp:
        json.dump(meta, fp, indent=2, sort_keys=True)
        fp.write("\n")
    os.replace(tmp, path)


def _load_meta(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        logger.debug("Skipping job %s without meta", path)
        return None
    try:
        with path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Unable to load job metadata (%s): %s", path, exc)
        return None
    if not isinstance(data, dict):
        logger.warning("Job metadata at %s is not an object; skipping", path)
        return None
    return data


@dataclass
class JobRecord:
    job_id: str
    git_owner: str
    repo_name: str
    branch_name: str
    inbox_file: str
    inbox_rel: str
    state: str
    retries: int
    created_at: str
    updated_at: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    processed_path: Optional[str] = None
    failure_reason: Optional[str] = None
    archived_path: Optional[str] = None
    job_dir: Path = field(default_factory=Path)

    @classmethod
    def from_disk(cls, job_dir: Path) -> Optional["JobRecord"]:
        state_path = job_dir / STATE_FILE
        meta_path = job_dir / META_FILE
        state = _load_state(state_path)
        if not state or state not in VALID_STATES:
            logger.warning(
                "Skipping job %s: invalid or missing state %r", job_dir, state
            )
            return None
        meta = _load_meta(meta_path)
        if meta is None:
            return None
        return cls(
            job_id=meta.get("job_id", job_dir.name),
            git_owner=meta.get("git_owner", ""),
            repo_name=meta.get("repo_name", ""),
            branch_name=meta.get("branch_name", ""),
            inbox_file=meta.get("inbox_file", ""),
            inbox_rel=meta.get("inbox_rel", ""),
            state=state,
            retries=int(meta.get("retries", 0)),
            created_at=meta.get("created_at", _utc_iso_now()),
            updated_at=meta.get("updated_at", _utc_iso_now()),
            metadata=dict(meta.get("metadata", {})),
            processed_path=meta.get("processed_path"),
            failure_reason=meta.get("failure_reason"),
            archived_path=meta.get("archived_path"),
            job_dir=job_dir,
        )

    def to_meta(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "git_owner": self.git_owner,
            "repo_name": self.repo_name,
            "branch_name": self.branch_name,
            "inbox_file": self.inbox_file,
            "inbox_rel": self.inbox_rel,
            "state": self.state,
            "retries": self.retries,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
            "processed_path": self.processed_path,
            "failure_reason": self.failure_reason,
            "archived_path": self.archived_path,
        }


def _persist_job(job: JobRecord) -> None:
    job.metadata = dict(job.metadata)
    job.updated_at = _utc_iso_now()
    _write_meta(job.job_dir / META_FILE, job.to_meta())
    _write_state(job.job_dir / STATE_FILE, job.state)


def _iter_job_dirs(root: Path) -> Iterator[Path]:
    if not root.is_dir():
        return
    for entry in root.iterdir():
        if entry.is_dir():
            yield entry


def enqueue_job(
    jobs_root: Path | str,
    *,
    git_owner: str,
    repo_name: str,
    branch_name: str,
    inbox_file: str,
    inbox_rel: str,
    reason: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> JobRecord:
    root = ensure_jobs_root(Path(jobs_root))
    job_id = uuid.uuid4().hex
    job_dir = _job_dir(root, job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    now = _utc_iso_now()
    data = {
        "job_id": job_id,
        "git_owner": git_owner,
        "repo_name": repo_name,
        "branch_name": branch_name,
        "inbox_file": inbox_file,
        "inbox_rel": inbox_rel,
        "state": STATE_QUEUED,
        "retries": 0,
        "created_at": now,
        "updated_at": now,
        "metadata": dict(metadata or {}),
        "processed_path": None,
        "failure_reason": None,
        "archived_path": None,
    }
    if reason:
        data["metadata"].setdefault("reason", reason)
    _write_meta(job_dir / META_FILE, data)
    _write_state(job_dir / STATE_FILE, STATE_QUEUED)
    job = JobRecord.from_disk(job_dir)
    if job is None:
        raise RuntimeError(f"Failed to load job after enqueueing: {job_dir}")
    return job


def _matched_job(
    job: JobRecord, *, state: Optional[str] = None, inbox_file: Optional[str] = None
) -> bool:
    if state and job.state != state:
        return False
    if inbox_file and job.inbox_file != inbox_file:
        return False
    return True


def get_next_queued_job(jobs_root: Path | str) -> Optional[JobRecord]:
    root = Path(jobs_root)
    candidates: List[Tuple[str, str, JobRecord]] = []
    for job_dir in _iter_job_dirs(root):
        job = JobRecord.from_disk(job_dir)
        if job is None:
            continue
        if job.state != STATE_QUEUED:
            continue
        candidates.append((job.created_at, job.job_id, job))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][2]


def find_job_for_inbox(jobs_root: Path | str, inbox_file: str) -> Optional[JobRecord]:
    root = Path(jobs_root)
    for job_dir in _iter_job_dirs(root):
        job = JobRecord.from_disk(job_dir)
        if job is None:
            continue
        if job.inbox_file == inbox_file:
            return job
    return None


def mark_running(job: JobRecord, *, reason: Optional[str] = None) -> JobRecord:
    job.state = STATE_RUNNING
    job.metadata["last_reason"] = reason or job.metadata.get("last_reason")
    _persist_job(job)
    return job


def mark_succeeded(job: JobRecord, *, processed_path: str) -> JobRecord:
    job.state = STATE_SUCCEEDED
    job.processed_path = processed_path
    job.metadata.pop("last_failure", None)
    _persist_job(job)
    return job


def mark_failed(
    job: JobRecord,
    *,
    retryable: bool,
    reason: Optional[str] = None,
    archived_path: Optional[str] = None,
) -> JobRecord:
    job.state = STATE_FAILED_RETRYABLE if retryable else STATE_FAILED_FINAL
    job.failure_reason = reason
    job.metadata["last_failure"] = reason
    job.archived_path = archived_path
    _persist_job(job)
    return job


def should_retry(job: JobRecord, max_retries: int) -> bool:
    return job.retries < max_retries


def requeue(job: JobRecord) -> JobRecord:
    job.state = STATE_QUEUED
    job.retries += 1
    job.metadata["last_retry"] = _utc_iso_now()
    _persist_job(job)
    return job
