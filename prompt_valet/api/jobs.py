"""Job metadata helpers for the Prompt Valet API."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

_LOGGER = logging.getLogger(__name__)

JOB_METADATA_FILENAME = "job.json"


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    payload: Dict[str, Any]
    state: str
    git_owner: str
    repo_name: str
    branch_name: str
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    heartbeat_at: Optional[datetime]
    stalled: bool
    age_seconds: float

    @property
    def repo_full(self) -> str:
        if self.git_owner and self.repo_name:
            return f"{self.git_owner}/{self.repo_name}"
        if self.repo_name:
            return self.repo_name
        return ""

    @property
    def state_lower(self) -> str:
        return (self.state or "").lower()

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = dict(self.payload)
        result["stalled"] = self.stalled
        result["age_seconds"] = self.age_seconds
        return result

    @classmethod
    def from_payload(
        cls,
        payload: Dict[str, Any],
        now: datetime,
        stall_threshold_seconds: int,
    ) -> Optional[JobRecord]:
        job_id = payload.get("job_id")
        if not job_id:
            _LOGGER.warning("Skipping job record with missing job_id: %s", payload)
            return None

        state = payload.get("state") or ""
        created_at = _parse_iso(payload.get("created_at"))
        started_at = _parse_iso(payload.get("started_at"))
        updated_at = _parse_iso(payload.get("updated_at"))
        heartbeat_at = _parse_iso(payload.get("heartbeat_at"))

        reference = created_at or started_at or updated_at
        age_seconds = _seconds_since(reference, now)

        git_owner = payload.get("git_owner") or ""
        repo_name = payload.get("repo_name") or ""
        branch_name = payload.get("branch_name") or ""

        stalled = False
        if state.lower() == "running" and heartbeat_at:
            stalled = (now - heartbeat_at).total_seconds() > stall_threshold_seconds

        return cls(
            job_id=job_id,
            payload=payload,
            state=state,
            git_owner=git_owner,
            repo_name=repo_name,
            branch_name=branch_name,
            created_at=created_at,
            updated_at=updated_at,
            heartbeat_at=heartbeat_at,
            stalled=stalled,
            age_seconds=age_seconds,
        )


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        _LOGGER.warning("Failed to parse timestamp %r", value)
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _seconds_since(instant: Optional[datetime], now: datetime) -> float:
    if instant is None:
        return 0.0
    delta = (now - instant).total_seconds()
    return max(delta, 0.0)


def list_job_records(
    runs_root: Path,
    stall_threshold_seconds: int,
    *,
    now: Optional[datetime] = None,
) -> List[JobRecord]:
    now = now or datetime.utcnow()
    if not runs_root.is_dir():
        return []

    records: List[JobRecord] = []
    for job_dir in runs_root.iterdir():
        if not job_dir.is_dir():
            continue
        job_file = job_dir / JOB_METADATA_FILENAME
        if not job_file.is_file():
            continue
        payload = _load_job_json(job_file)
        if payload is None:
            continue
        record = JobRecord.from_payload(payload, now, stall_threshold_seconds)
        if record is None:
            continue
        records.append(record)

    return sorted(records, key=_job_sort_key, reverse=True)


def get_job_record(
    job_id: str,
    runs_root: Path,
    stall_threshold_seconds: int,
    *,
    now: Optional[datetime] = None,
) -> Optional[JobRecord]:
    now = now or datetime.utcnow()
    job_dir = runs_root / job_id
    job_file = job_dir / JOB_METADATA_FILENAME
    if not job_file.is_file():
        return None
    payload = _load_job_json(job_file)
    if payload is None:
        return None
    return JobRecord.from_payload(payload, now, stall_threshold_seconds)


def _load_job_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        _LOGGER.warning("Skipping invalid job metadata %s", path)
    except OSError as exc:
        _LOGGER.warning("Failed to read job metadata %s: %s", path, exc)
    return None


def _job_sort_key(record: JobRecord) -> float:
    if record.created_at:
        return record.created_at.timestamp()
    if record.updated_at:
        return record.updated_at.timestamp()
    if record.heartbeat_at:
        return record.heartbeat_at.timestamp()
    return 0.0


def filter_jobs(
    records: Iterable[JobRecord],
    *,
    state: str | None = None,
    repo: str | None = None,
    branch: str | None = None,
    stalled: bool | None = None,
) -> List[JobRecord]:
    filtered = []
    for record in records:
        if state and record.state_lower != state.lower():
            continue
        if repo and not _matches_repo_filter(record, repo):
            continue
        if branch and record.branch_name != branch:
            continue
        if stalled is not None and record.stalled != stalled:
            continue
        filtered.append(record)
    return filtered


def _matches_repo_filter(record: JobRecord, repo_filter: str) -> bool:
    full = record.repo_full
    if repo_filter == full:
        return True
    if repo_filter == record.repo_name:
        return True
    return False


__all__ = [
    "JobRecord",
    "list_job_records",
    "get_job_record",
    "filter_jobs",
]
