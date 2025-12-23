from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from prompt_valet.api.config import APISettings
from prompt_valet.api.discovery import list_targets
from prompt_valet.api.jobs import (
    get_job_record,
    list_job_records,
    filter_jobs,
    JOB_METADATA_FILENAME,
)


def _make_settings(
    inbox_root: Path, inbox_mode: str = "legacy_single_owner"
) -> APISettings:
    return APISettings(
        tree_builder_root=inbox_root,
        runs_root=inbox_root.parent / "runs",
        stall_threshold_seconds=60,
        bind_host="localhost",
        bind_port=8000,
        git_owner="nova-rey",
        inbox_mode=inbox_mode,
    )


def test_discovery_respects_legacy_layout(tmp_path: Path) -> None:
    inbox_root = tmp_path / "inbox"
    branch_dir = inbox_root / "alpha" / "main"
    branch_dir.mkdir(parents=True)
    (branch_dir / "prompt.prompt.md").write_text("test")

    settings = _make_settings(inbox_root)
    targets = list_targets(settings)

    assert len(targets) == 1
    target = targets[0]
    assert target.repo == "alpha"
    assert target.branch == "main"
    assert target.owner == "nova-rey"


def test_discovery_handles_multi_owner(tmp_path: Path) -> None:
    inbox_root = tmp_path / "inbox"
    feature_dir = inbox_root / "ownerX" / "beta" / "feature"
    feature_dir.mkdir(parents=True)
    (feature_dir / "prompt.prompt.md").write_text("test")

    settings = _make_settings(inbox_root, inbox_mode="multi_owner")
    targets = list_targets(settings)

    assert len(targets) == 1
    target = targets[0]
    assert target.owner == "ownerX"
    assert target.repo == "beta"
    assert target.branch == "feature"


def _write_job(runs_root: Path, job_id: str, payload: dict[str, object]) -> None:
    job_dir = runs_root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    job_file = job_dir / JOB_METADATA_FILENAME
    job_file.write_text(json.dumps(payload), encoding="utf-8")


def test_job_listing_filters_stalled_and_skips_invalid(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    now = datetime(2025, 1, 1, 0, 0, 30)

    recent = {
        "job_id": "recent",
        "git_owner": "nova",
        "repo_name": "alpha",
        "branch_name": "main",
        "state": "running",
        "created_at": "2025-01-01T00:00:20Z",
        "heartbeat_at": "2025-01-01T00:00:25Z",
    }
    stalled = {
        "job_id": "stalled",
        "git_owner": "nova",
        "repo_name": "alpha",
        "branch_name": "main",
        "state": "running",
        "created_at": "2025-01-01T00:00:00Z",
        "heartbeat_at": "2024-12-31T23:59:00Z",
    }
    _write_job(runs_root, "recent", recent)
    _write_job(runs_root, "stalled", stalled)

    bad_dir = runs_root / "broken"
    bad_dir.mkdir(parents=True)
    (bad_dir / JOB_METADATA_FILENAME).write_text("not json", encoding="utf-8")

    records = list_job_records(runs_root, stall_threshold_seconds=10, now=now)
    assert any(record.job_id == "stalled" and record.stalled for record in records)
    assert any(record.job_id == "recent" and not record.stalled for record in records)
    assert len(records) == 2

    filtered = filter_jobs(records, repo="nova/alpha", stalled=True)
    assert len(filtered) == 1
    assert filtered[0].job_id == "stalled"

    detail = get_job_record("stalled", runs_root, 10, now=now)
    assert detail is not None
    assert detail.stalled

    assert get_job_record("missing", runs_root, 10, now=now) is None


def test_list_job_records_handles_zero_runs(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    assert list_job_records(runs_root, stall_threshold_seconds=5) == []
