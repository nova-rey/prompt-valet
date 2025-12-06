from pathlib import Path
import logging
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import codex_watcher


def test_ensure_worker_repo_clean_and_synced_dirty_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    responses = [
        {"args": ["fetch", "--prune"], "rc": 0, "stdout": "", "stderr": ""},
        {
            "args": ["status", "--porcelain"],
            "rc": 0,
            "stdout": " M docs/example.md\n",
            "stderr": "",
        },
        {"args": ["fetch", "--prune"], "rc": 0, "stdout": "", "stderr": ""},
        {
            "args": ["reset", "--hard", "origin/main"],
            "rc": 0,
            "stdout": "",
            "stderr": "",
        },
        {"args": ["clean", "-fdx"], "rc": 0, "stdout": "", "stderr": ""},
    ]
    expected_calls = [r["args"] for r in responses]

    calls = []

    def fake_run_git(args, *, cwd, logger, check=False):
        calls.append(list(args))
        resp = responses.pop(0)

        class P:
            returncode = resp["rc"]
            stdout = resp.get("stdout", "")
            stderr = resp.get("stderr", "")

        return P()

    monkeypatch.setattr(codex_watcher, "_run_git", fake_run_git)
    logger = logging.getLogger("test")

    ok = codex_watcher.ensure_worker_repo_clean_and_synced(repo, "main", logger)

    assert ok is True
    assert calls == expected_calls


def test_ensure_worker_repo_clean_and_synced_clean_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    responses = [
        {"args": ["fetch", "--prune"], "rc": 0, "stdout": "", "stderr": ""},
        {"args": ["status", "--porcelain"], "rc": 0, "stdout": "", "stderr": ""},
        {"args": ["pull", "--rebase", "--autostash"], "rc": 0, "stdout": "", "stderr": ""},
    ]
    expected_calls = [r["args"] for r in responses]

    calls = []

    def fake_run_git(args, *, cwd, logger, check=False):
        calls.append(list(args))
        resp = responses.pop(0)

        class P:
            returncode = resp["rc"]
            stdout = resp.get("stdout", "")
            stderr = resp.get("stderr", "")

        return P()

    monkeypatch.setattr(codex_watcher, "_run_git", fake_run_git)
    logger = logging.getLogger("test")

    ok = codex_watcher.ensure_worker_repo_clean_and_synced(repo, "main", logger)

    assert ok is True
    assert calls == expected_calls


def test_ensure_worker_repo_clean_and_synced_fetch_failure(tmp_path, monkeypatch, caplog):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    responses = [
        {"args": ["fetch", "--prune"], "rc": 1, "stdout": "", "stderr": "nope"},
    ]

    def fake_run_git(args, *, cwd, logger, check=False):
        resp = responses.pop(0)

        class P:
            returncode = resp["rc"]
            stdout = resp.get("stdout", "")
            stderr = resp.get("stderr", "")

        return P()

    monkeypatch.setattr(codex_watcher, "_run_git", fake_run_git)
    logger = logging.getLogger("test")

    with caplog.at_level(logging.ERROR):
        ok = codex_watcher.ensure_worker_repo_clean_and_synced(repo, "main", logger)

    assert ok is False
    assert "git fetch --prune" in caplog.text
