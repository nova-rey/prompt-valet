from pathlib import Path

import os
import queue
import time
from pathlib import Path

from scripts import codex_watcher


def test_statusified_name_prompt_to_running():
    assert (
        codex_watcher._statusified_name(
            "xyz.prompt.md", codex_watcher.STATUS_RUNNING
        )
        == "xyz.running.md"
    )
    assert (
        codex_watcher._statusified_name(
            "foo.txt", codex_watcher.STATUS_RUNNING
        )
        == "foo.running.txt"
    )


def test_claim_and_finalize_lifecycle(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    finished = tmp_path / "finished"
    inbox.mkdir()
    finished.mkdir()

    rel = Path("prompt-valet/main/xyz.prompt.md")
    src = inbox / rel
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("# test")

    running_path = codex_watcher.claim_inbox_prompt(inbox, rel)
    assert running_path.name == "xyz.running.md"
    assert running_path.exists()
    assert not src.exists()

    codex_watcher.finalize_inbox_prompt(
        inbox_root=inbox,
        finished_root=finished,
        rel=rel,
        status=codex_watcher.STATUS_DONE,
        delay_seconds=0.0,
    )

    finished_path = finished / rel.with_name("xyz.done.md")
    assert finished_path.exists()
    assert not running_path.exists()


def test_claim_new_prompts_debounce(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()

    rel = Path("prompt-valet/main/abc.prompt.md")
    prompt = inbox / rel
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text("# debounce test")

    codex_watcher.claim_new_prompts(inbox)
    assert prompt.exists()
    assert not (prompt.parent / "abc.running.md").exists()

    old = time.time() - (codex_watcher.DEBOUNCE_SECONDS + 1)
    os.utime(prompt, (old, old))

    codex_watcher.claim_new_prompts(inbox)
    assert not prompt.exists()
    assert (prompt.parent / "abc.running.md").exists()


def test_two_phase_running_transition(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    processed = tmp_path / "processed"
    finished = tmp_path / "finished"
    inbox.mkdir()
    processed.mkdir()
    finished.mkdir()

    config = codex_watcher.load_config_from_dict(
        {
            "inbox": str(inbox),
            "processed": str(processed),
            "finished": str(finished),
            "repos_root": str(tmp_path / "repos"),
            "git_owner": "prompt-valet",
        }
    )
    codex_watcher.CONFIG = config
    codex_watcher.JOB_STATES.clear()
    codex_watcher.INBOX_MODE = config.get("inbox_mode", "legacy_single_owner")

    rel = Path("prompt-valet/main/sample.prompt.md")
    prompt = inbox / rel
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text("# sample")
    old = time.time() - (codex_watcher.DEBOUNCE_SECONDS + 1)
    os.utime(prompt, (old, old))

    codex_watcher.claim_new_prompts(inbox)
    running_path = prompt.with_name("sample.running.md")
    assert running_path.exists()

    job_queue: "queue.Queue[codex_watcher.Job]" = queue.Queue()
    codex_watcher.start_jobs_from_running(inbox, processed, job_queue)

    assert job_queue.qsize() == 1
    job = job_queue.get_nowait()
    key = codex_watcher._job_key(job.inbox_rel)

    assert codex_watcher.JOB_STATES[key] == codex_watcher.STATUS_RUNNING
    assert job.prompt_path.exists()
    assert running_path.exists()


def test_start_jobs_idempotent(tmp_path):
    inbox = tmp_path / "inbox"
    processed = tmp_path / "processed"
    finished = tmp_path / "finished"
    inbox.mkdir()
    processed.mkdir()
    finished.mkdir()

    config = codex_watcher.load_config_from_dict(
        {
            "inbox": str(inbox),
            "processed": str(processed),
            "finished": str(finished),
            "repos_root": str(tmp_path / "repos"),
            "git_owner": "prompt-valet",
        }
    )
    codex_watcher.CONFIG = config
    codex_watcher.JOB_STATES.clear()
    codex_watcher.INBOX_MODE = config.get("inbox_mode", "legacy_single_owner")

    rel = Path("prompt-valet/main/sample.prompt.md")
    prompt = inbox / rel
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text("# sample")
    old = time.time() - (codex_watcher.DEBOUNCE_SECONDS + 1)
    os.utime(prompt, (old, old))

    codex_watcher.claim_new_prompts(inbox)
    job_queue: "queue.Queue[codex_watcher.Job]" = queue.Queue()
    codex_watcher.start_jobs_from_running(inbox, processed, job_queue)

    first_count = job_queue.qsize()
    codex_watcher.start_jobs_from_running(inbox, processed, job_queue)
    assert job_queue.qsize() == first_count
