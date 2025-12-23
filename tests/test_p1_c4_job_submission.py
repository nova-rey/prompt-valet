from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from fastapi.testclient import TestClient

from prompt_valet.api.app import create_app
from prompt_valet.api.config import APISettings


def _make_settings(inbox_root: Path) -> APISettings:
    return APISettings(
        tree_builder_root=inbox_root,
        runs_root=inbox_root.parent / "runs",
        stall_threshold_seconds=60,
        bind_host="localhost",
        bind_port=8000,
        git_owner="nova-rey",
        inbox_mode="legacy_single_owner",
    )


def _extract_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    assert content.startswith("---"), "Expected frontmatter at top of file"
    lines = content.splitlines(keepends=True)
    assert lines[0].strip() == "---"
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            raw = "".join(lines[1:index])
            body = "".join(lines[index + 1 :])
            data = yaml.safe_load(raw) or {}
            assert isinstance(data, dict)
            return data, body
    raise AssertionError("Missing closing frontmatter marker")


def _make_client(tmp_path: Path) -> TestClient:
    inbox_root = tmp_path / "inbox"
    (inbox_root / "alpha" / "main").mkdir(parents=True)
    settings = _make_settings(inbox_root)
    return TestClient(create_app(settings))


def test_job_submission_creates_markdown(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    response = client.post(
        "/api/v1/jobs",
        json={
            "repo": "alpha",
            "branch": "main",
            "filename": "greet.prompt.md",
            "markdown_text": "Hello world",
        },
    )
    assert response.status_code == 201
    payload = response.json()
    job_id = payload["job_id"]
    inbox_path = Path(payload["inbox_path"])
    assert inbox_path.exists()
    assert job_id in inbox_path.name
    assert inbox_path.name.endswith(".prompt.md")
    frontmatter, body = _extract_frontmatter(inbox_path.read_text())
    pv = frontmatter.get("pv") or {}
    assert pv["job_id"] == job_id
    assert pv["repo"] == "nova-rey/alpha"
    assert pv["branch"] == "main"
    assert "source" in pv and pv["source"] == "api"
    assert "Hello world" in body


def test_frontmatter_merge_preserves_existing_fields(tmp_path: Path) -> None:
    inbox_root = tmp_path / "inbox"
    (inbox_root / "beta" / "main").mkdir(parents=True)
    client = TestClient(create_app(_make_settings(inbox_root)))
    response = client.post(
        "/api/v1/jobs",
        json={
            "repo": "beta",
            "branch": "main",
            "filename": "existing.prompt.md",
            "markdown_text": """---
title: Example
pv:
  note: keep
---
body text
""",
        },
    )
    assert response.status_code == 201
    payload = response.json()
    frontmatter, _ = _extract_frontmatter(Path(payload["inbox_path"]).read_text())
    assert frontmatter["title"] == "Example"
    pv = frontmatter["pv"]
    assert pv["note"] == "keep"
    assert pv["job_id"] == payload["job_id"]


def test_invalid_repo_branch_rejected(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    response = client.post(
        "/api/v1/jobs",
        json={
            "repo": "missing",
            "branch": "main",
            "markdown_text": "nope",
        },
    )
    assert response.status_code == 404


def test_upload_rejects_non_md_files(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    response = client.post(
        "/api/v1/jobs/upload",
        data={"repo": "alpha", "branch": "main"},
        files=[("files", ("bad.txt", "content"))],
    )
    assert response.status_code == 400


def test_upload_creates_job(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    response = client.post(
        "/api/v1/jobs/upload",
        data={"repo": "alpha", "branch": "main"},
        files=[("files", ("upload.prompt.md", "api body\n"))],
    )
    assert response.status_code == 201
    content = response.json()
    assert "jobs" in content and isinstance(content["jobs"], list)
    assert content["jobs"]
    job = content["jobs"][0]
    inbox_path = Path(job["inbox_path"])
    assert inbox_path.exists()
    frontmatter, body = _extract_frontmatter(inbox_path.read_text())
    pv = frontmatter["pv"]
    assert pv["job_id"] == job["job_id"]
    assert pv["source"] == "api"
    assert body.strip() == "api body"
