from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from prompt_valet.api.app import create_app
from prompt_valet.api.config import APISettings


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_settings(runs_root: Path) -> APISettings:
    tree_root = runs_root / "tree"
    tree_root.mkdir(parents=True, exist_ok=True)
    return APISettings(
        tree_builder_root=tree_root,
        runs_root=runs_root,
        stall_threshold_seconds=60,
        bind_host="localhost",
        bind_port=8000,
        git_owner="nova-rey",
        inbox_mode="legacy_single_owner",
    )


def _write_job_payload(
    runs_root: Path, job_id: str, payload: dict[str, object]
) -> Path:
    job_dir = runs_root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    job_file = job_dir / "job.json"
    job_file.write_text(json.dumps(payload), encoding="utf-8")
    return job_dir


def _base_payload(job_id: str, log_path: str, state: str) -> dict[str, object]:
    now = _now_iso()
    return {
        "job_id": job_id,
        "git_owner": "nova-rey",
        "repo_name": "alpha",
        "branch_name": "main",
        "state": state,
        "created_at": now,
        "updated_at": now,
        "heartbeat_at": now,
        "log_path": log_path,
        "metadata": {},
    }


def test_job_log_tail_returns_recent_lines(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    job_id = "logtail"
    job_dir = runs_root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    log_path = job_dir / "job.log"
    log_path.write_text("line1\nline2\nline3\n", encoding="utf-8")
    _write_job_payload(
        runs_root,
        job_id,
        _base_payload(job_id, str(log_path), "succeeded"),
    )

    client = TestClient(create_app(_make_settings(runs_root)))
    response = client.get(f"/api/v1/jobs/{job_id}/log", params={"lines": 2})
    assert response.status_code == 200
    assert response.text == "line2\nline3\n"


def test_job_log_stream_produces_sse(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    job_id = "logstream"
    job_dir = runs_root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    log_path = job_dir / "job.log"
    log_path.write_text("first\nsecond\n", encoding="utf-8")
    _write_job_payload(
        runs_root,
        job_id,
        _base_payload(job_id, str(log_path), "succeeded"),
    )

    client = TestClient(create_app(_make_settings(runs_root)))
    response = client.get(f"/api/v1/jobs/{job_id}/log/stream")
    assert response.status_code == 200
    text = response.text
    assert "data: first" in text
    assert "data: second" in text


def test_abort_endpoint_creates_marker(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    job_id = "abortable"
    job_dir = runs_root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    log_path = job_dir / "job.log"
    log_path.write_text("", encoding="utf-8")
    _write_job_payload(
        runs_root,
        job_id,
        _base_payload(job_id, str(log_path), "running"),
    )

    client = TestClient(create_app(_make_settings(runs_root)))
    response = client.post(f"/api/v1/jobs/{job_id}/abort")
    assert response.status_code == 200
    assert (job_dir / "ABORT").exists()
    assert response.json()["previous_state"] == "running"
    second = client.post(f"/api/v1/jobs/{job_id}/abort")
    assert second.status_code == 200
    assert (job_dir / "ABORT").exists()


def test_abort_on_terminal_job_rejected(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    job_id = "already-done"
    job_dir = runs_root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    log_path = job_dir / "job.log"
    log_path.write_text("", encoding="utf-8")
    _write_job_payload(
        runs_root,
        job_id,
        _base_payload(job_id, str(log_path), "succeeded"),
    )

    client = TestClient(create_app(_make_settings(runs_root)))
    response = client.post(f"/api/v1/jobs/{job_id}/abort")
    assert response.status_code == 409
    assert not (job_dir / "ABORT").exists()


def test_job_log_errors_when_missing(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    job_id = "missing-log"
    job_dir = runs_root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    _write_job_payload(
        runs_root,
        job_id,
        _base_payload(job_id, str(job_dir / "job.log"), "running"),
    )

    client = TestClient(create_app(_make_settings(runs_root)))
    response = client.get(f"/api/v1/jobs/{job_id}/log")
    assert response.status_code == 404
