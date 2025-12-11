import pytest
from pathlib import Path

from scripts import pv_jobs


def test_ensure_jobs_root_creates_all_buckets(tmp_path: Path) -> None:
    root = tmp_path / "jobs"
    result = pv_jobs.ensure_jobs_root(root)

    assert result == root
    for status in pv_jobs.VALID_STATUSES:
        bucket = root / status
        assert bucket.exists()
        assert bucket.is_dir()


def test_create_and_list_job_round_trip(tmp_path: Path) -> None:
    root = tmp_path / "jobs"

    job = pv_jobs.create_job(
        root,
        repo="ExampleRepo",
        branch="main",
        logical_prompt="P1.prompt.md",
        prompt_path="ExampleRepo/main/P1.prompt.md",
        prompt_sha256="deadbeef" * 8,
        base_commit="abc123",
        attempt=1,
    )

    assert job.job_id
    assert job.status == pv_jobs.JOB_STATUS_PENDING

    all_jobs = pv_jobs.list_jobs(root)
    assert len(all_jobs) == 1
    listed = all_jobs[0]

    assert listed.job_id == job.job_id
    assert listed.repo == "ExampleRepo"
    assert listed.branch == "main"
    assert listed.logical_prompt == "P1.prompt.md"
    assert listed.prompt_path == "ExampleRepo/main/P1.prompt.md"
    assert listed.prompt_sha256 == "deadbeef" * 8
    assert listed.base_commit == "abc123"
    assert listed.status == pv_jobs.JOB_STATUS_PENDING


def test_find_job_by_id_and_status_transition(tmp_path: Path) -> None:
    root = tmp_path / "jobs"

    created = pv_jobs.create_job(
        root,
        repo="ExampleRepo",
        branch="feature/x",
        logical_prompt="P2.prompt.md",
        prompt_path="ExampleRepo/feature-x/P2.prompt.md",
        prompt_sha256="cafebabe" * 8,
        base_commit=None,
    )

    from_lookup = pv_jobs.find_job_by_id(root, created.job_id)
    assert from_lookup is not None
    assert from_lookup.job_id == created.job_id
    assert from_lookup.status == pv_jobs.JOB_STATUS_PENDING

    updated = pv_jobs.mark_job_status(
        root,
        created.job_id,
        pv_jobs.JOB_STATUS_RUNNING,
        extra_fields={"attempt": 2, "metadata_key": "value"},
    )

    assert updated.status == pv_jobs.JOB_STATUS_RUNNING
    assert updated.attempt == 2
    assert updated.metadata.get("metadata_key") == "value"

    # The file should live under the new bucket only.
    for status in pv_jobs.VALID_STATUSES:
        path = root / status / f"{created.job_id}.json"
        if status == pv_jobs.JOB_STATUS_RUNNING:
            assert path.exists()
        else:
            assert not path.exists()


def test_list_jobs_filters_by_status(tmp_path: Path) -> None:
    root = tmp_path / "jobs"

    job1 = pv_jobs.create_job(
        root,
        repo="ExampleRepo",
        branch="main",
        logical_prompt="P1.prompt.md",
        prompt_path="ExampleRepo/main/P1.prompt.md",
        prompt_sha256="1" * 64,
        base_commit=None,
    )
    job2 = pv_jobs.create_job(
        root,
        repo="ExampleRepo",
        branch="main",
        logical_prompt="P2.prompt.md",
        prompt_path="ExampleRepo/main/P2.prompt.md",
        prompt_sha256="2" * 64,
        base_commit=None,
    )

    pv_jobs.mark_job_status(root, job2.job_id, pv_jobs.JOB_STATUS_FAILED)

    pending = pv_jobs.list_jobs(root, status=pv_jobs.JOB_STATUS_PENDING)
    failed = pv_jobs.list_jobs(root, status=pv_jobs.JOB_STATUS_FAILED)
    all_jobs = pv_jobs.list_jobs(root)

    pending_ids = {j.job_id for j in pending}
    failed_ids = {j.job_id for j in failed}
    all_ids = {j.job_id for j in all_jobs}

    assert job1.job_id in pending_ids
    assert job2.job_id not in pending_ids

    assert job2.job_id in failed_ids
    assert job1.job_id not in failed_ids

    assert pending_ids.union(failed_ids).issubset(all_ids)


def test_job_from_inbox_path_extracts_branch(tmp_path: Path) -> None:
    inbox_root = tmp_path / "inbox"
    prompt_path = inbox_root / "demo-repo" / "API" / "Test.prompt.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text("prompt contents")

    job = pv_jobs.Job.from_inbox_path(
        prompt_path,
        inbox_root=inbox_root,
        prompt_sha256="deadbeef" * 8,
    )

    assert job.repo_name == "demo-repo"
    assert job.branch_name == "API"


def test_job_from_inbox_path_requires_branch_segment(tmp_path: Path) -> None:
    inbox_root = tmp_path / "inbox"
    prompt_path = inbox_root / "demo-repo" / "Test.prompt.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text("prompt contents")

    with pytest.raises(ValueError):
        pv_jobs.Job.from_inbox_path(
            prompt_path,
            inbox_root=inbox_root,
            prompt_sha256="deadbeef" * 8,
        )
