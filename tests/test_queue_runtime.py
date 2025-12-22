import json

from scripts import queue_runtime


def test_enqueue_job_creates_files(tmp_path):
    root = tmp_path / "jobs"
    job = queue_runtime.enqueue_job(
        root,
        git_owner="owner",
        repo_name="repo",
        branch_name="main",
        inbox_file="/inbox/repo/branch/foo.running.md",
        inbox_rel="repo/branch/foo.prompt.md",
        reason="new-prompt",
    )

    job_dir = root / job.job_id
    assert job_dir.is_dir()
    assert (
        job_dir / queue_runtime.STATE_FILE
    ).read_text().strip() == queue_runtime.STATE_QUEUED
    metadata = json.loads((job_dir / queue_runtime.JOB_FILE).read_text())
    assert metadata["git_owner"] == "owner"
    assert metadata["retries"] == 0
    assert metadata["metadata"]["reason"] == "new-prompt"


def test_mark_states_and_requeue(tmp_path):
    root = tmp_path / "jobs"
    job = queue_runtime.enqueue_job(
        root,
        git_owner="owner",
        repo_name="repo",
        branch_name="feat",
        inbox_file="/inbox/repo/feat/foo.running.md",
        inbox_rel="repo/feat/foo.prompt.md",
    )

    job = queue_runtime.mark_running(job, reason="started")
    assert job.state == queue_runtime.STATE_RUNNING
    assert job.metadata["last_reason"] == "started"

    job = queue_runtime.mark_failed(job, retryable=True, reason="git-error")
    assert job.state == queue_runtime.STATE_FAILED_RETRYABLE
    assert job.failure_reason == "git-error"
    assert queue_runtime.should_retry(job, max_retries=2)

    job = queue_runtime.requeue(job)
    assert job.state == queue_runtime.STATE_QUEUED
    assert job.retries == 1
    assert job.metadata["last_retry"] is not None

    job = queue_runtime.mark_failed(job, retryable=False, reason="final")
    assert job.state == queue_runtime.STATE_FAILED_FINAL
    assert job.failure_reason == "final"
    assert not queue_runtime.should_retry(job, max_retries=0)


def test_next_queued_job_orders_by_creation(monkeypatch, tmp_path):
    root = tmp_path / "jobs"
    times = iter(
        [
            "2020-01-01T00:00:00Z",
            "2020-01-02T00:00:00Z",
        ]
    )

    monkeypatch.setattr(
        queue_runtime,
        "_utc_iso_now",
        lambda: next(times, "2020-01-03T00:00:00Z"),
    )
    job1 = queue_runtime.enqueue_job(
        root,
        git_owner="owner",
        repo_name="repo",
        branch_name="main",
        inbox_file="/inbox/a.running.md",
        inbox_rel="a.prompt.md",
    )
    queue_runtime.enqueue_job(
        root,
        git_owner="owner",
        repo_name="repo",
        branch_name="main",
        inbox_file="/inbox/b.running.md",
        inbox_rel="b.prompt.md",
    )

    next_job = queue_runtime.get_next_queued_job(root)
    assert next_job is not None
    assert next_job.job_id == job1.job_id

    queue_runtime.mark_running(next_job)
    queue_runtime.mark_failed(next_job, retryable=True, reason="retry")
    job = queue_runtime.requeue(next_job)
    assert job.state == queue_runtime.STATE_QUEUED
    assert job.retries == 1
    assert queue_runtime.get_next_queued_job(root).job_id == job.job_id


def test_find_job_for_inbox(tmp_path):
    root = tmp_path / "jobs"
    job = queue_runtime.enqueue_job(
        root,
        git_owner="owner",
        repo_name="repo",
        branch_name="main",
        inbox_file="/tmp/inbox/foo.running.md",
        inbox_rel="foo.prompt.md",
    )

    from_disk = queue_runtime.find_job_for_inbox(root, job.inbox_file)
    assert from_disk is not None
    assert from_disk.job_id == job.job_id


def test_get_next_queued_job_skips_invalid(tmp_path):
    root = tmp_path / "jobs"
    bad = root / "bad"
    bad.mkdir(parents=True)
    (bad / queue_runtime.JOB_FILE).write_text("not-json")

    assert queue_runtime.get_next_queued_job(root) is None
