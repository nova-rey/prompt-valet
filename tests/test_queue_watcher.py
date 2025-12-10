import os
import queue
import time
from pathlib import Path

from scripts import codex_watcher, queue_runtime


def _setup_config(tmp_path, *, queue_enabled=False, **queue_overrides):
    config = {
        "pv_root": str(tmp_path / "pv"),
        "inbox": str(tmp_path / "inbox"),
        "processed": str(tmp_path / "processed"),
        "finished": str(tmp_path / "finished"),
        "failed": str(tmp_path / "failed"),
        "repos_root": str(tmp_path / "repos"),
        "git_owner": "owner",
        "queue": {
            "enabled": queue_enabled,
            "max_retries": queue_overrides.get("max_retries", 3),
            "failure_archive": queue_overrides.get("failure_archive", False),
        },
    }
    config.update(queue_overrides.get("extra", {}))
    cfg = codex_watcher.load_config_from_dict(config)
    codex_watcher.CONFIG = cfg
    codex_watcher.INBOX_MODE = cfg.get("inbox_mode", "legacy_single_owner")
    codex_watcher.JOB_STATES.clear()
    for path in (
        tmp_path / "inbox",
        tmp_path / "processed",
        tmp_path / "finished",
        tmp_path / "failed",
        tmp_path / "repos",
    ):
        path.mkdir(parents=True, exist_ok=True)
    return cfg


def _claim_prompt(tmp_path, rel: Path):
    prompt = tmp_path / "inbox" / rel
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text("# content")
    past = time.time() - (codex_watcher.DEBOUNCE_SECONDS + 1)
    os.utime(prompt, (past, past))
    codex_watcher.claim_new_prompts(tmp_path / "inbox")
    running = (tmp_path / "inbox" / rel).with_name(
        codex_watcher._statusified_name(rel.name, codex_watcher.STATUS_RUNNING)
    )
    assert running.exists()
    return running


def test_non_queue_mode_uses_direct_runner(tmp_path, monkeypatch):
    cfg = _setup_config(tmp_path, queue_enabled=False)
    running = _claim_prompt(tmp_path, Path("repo/main/prompt.prompt.md"))

    called = []

    def fake_run(job):
        called.append(job.job_id)
        return True

    monkeypatch.setattr(codex_watcher, "run_prompt_job", fake_run)

    job_queue: "queue.Queue[codex_watcher.Job]" = queue.Queue()
    codex_watcher.start_jobs_from_running(
        tmp_path / "inbox",
        tmp_path / "processed",
        job_queue,
        queue_enabled=False,
        queue_root=None,
    )

    job = job_queue.get_nowait()
    assert codex_watcher.run_prompt_job(job)
    codex_watcher.JOB_STATES[codex_watcher._job_key(job.inbox_rel)] = codex_watcher.STATUS_DONE
    codex_watcher.finalize_inbox_prompt(
        inbox_root=tmp_path / "inbox",
        finished_root=tmp_path / "finished",
        rel=job.inbox_rel,
        status=codex_watcher.STATUS_DONE,
        delay_seconds=0.0,
    )

    assert called
    queue_root = Path(cfg["pv_root"]) / ".queue"
    assert not queue_root.exists()


def test_queue_executor_processes_job(tmp_path, monkeypatch):
    cfg = _setup_config(tmp_path, queue_enabled=True, max_retries=2)
    running = _claim_prompt(tmp_path, Path("repo/main/prompt.prompt.md"))

    ran = []

    def fake_run(job):
        ran.append(job.job_id)
        return True

    monkeypatch.setattr(codex_watcher, "run_prompt_job", fake_run)

    codex_watcher.start_jobs_from_running(
        tmp_path / "inbox",
        tmp_path / "processed",
        None,
        queue_enabled=True,
        queue_root=codex_watcher._queue_root_from_config(cfg),
    )

    queue_root = codex_watcher._queue_root_from_config(cfg)
    assert queue_runtime.get_next_queued_job(queue_root) is not None

    job_record = queue_runtime.get_next_queued_job(queue_root)
    assert job_record is not None
    codex_watcher._process_queue_job(
        job_record,
        processed_root=tmp_path / "processed",
        failed_root=tmp_path / "failed",
        failure_archive=False,
        max_retries=cfg["queue"]["max_retries"],
    )

    job_record = queue_runtime.find_job_for_inbox(queue_root, str(running))
    assert job_record is not None
    assert job_record.state == queue_runtime.STATE_SUCCEEDED
    assert job_record.processed_path
    assert Path(job_record.processed_path).exists()
    assert not running.exists()
    assert ran


def test_queue_executor_requeues_on_retryable_failure(tmp_path, monkeypatch):
    cfg = _setup_config(tmp_path, queue_enabled=True, max_retries=1)
    running = _claim_prompt(tmp_path, Path("repo/main/when.prompt.md"))

    def fake_run(job):
        return False

    monkeypatch.setattr(codex_watcher, "run_prompt_job", fake_run)

    queue_root = codex_watcher._queue_root_from_config(cfg)
    codex_watcher.start_jobs_from_running(
        tmp_path / "inbox",
        tmp_path / "processed",
        None,
        queue_enabled=True,
        queue_root=queue_root,
    )

    job_record = queue_runtime.get_next_queued_job(queue_root)
    assert job_record is not None
    codex_watcher._process_queue_job(
        job_record,
        processed_root=tmp_path / "processed",
        failed_root=tmp_path / "failed",
        failure_archive=False,
        max_retries=cfg["queue"]["max_retries"],
    )

    job_record = queue_runtime.find_job_for_inbox(queue_root, str(running))
    assert job_record is not None
    assert job_record.state == queue_runtime.STATE_QUEUED
    assert job_record.retries == 1
    assert running.exists()


def test_queue_executor_archives_final_failure(tmp_path, monkeypatch):
    cfg = _setup_config(
        tmp_path,
        queue_enabled=True,
        max_retries=0,
        failure_archive=True,
    )
    running = _claim_prompt(tmp_path, Path("repo/main/error.prompt.md"))

    def fail_run(job):
        raise RuntimeError("codex fail")

    monkeypatch.setattr(codex_watcher, "run_prompt_job", fail_run)

    queue_root = codex_watcher._queue_root_from_config(cfg)
    codex_watcher.start_jobs_from_running(
        tmp_path / "inbox",
        tmp_path / "processed",
        None,
        queue_enabled=True,
        queue_root=queue_root,
    )

    codex_watcher._drain_queue_once(
        queue_root,
        tmp_path / "processed",
        tmp_path / "failed",
        failure_archive=True,
        max_retries=cfg["queue"]["max_retries"],
    )

    job_record = queue_runtime.find_job_for_inbox(queue_root, str(running))
    assert job_record is not None
    assert job_record.state == queue_runtime.STATE_FAILED_FINAL
    assert job_record.archived_path
    failed_path = Path(job_record.archived_path)
    assert failed_path.exists()
    assert not running.exists()
