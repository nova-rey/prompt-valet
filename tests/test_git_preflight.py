from pathlib import Path
import logging
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import codex_watcher


def _fake_ensure_repo_cloned(repo_root: Path, git_owner: str, repo_name: str) -> Path:
    """Create a minimal repo structure without hitting the network."""
    target = repo_root / git_owner / repo_name
    target.mkdir(parents=True, exist_ok=True)
    (target / ".git").mkdir(parents=True, exist_ok=True)
    return target


def test_ensure_worker_repo_clean_and_synced_dirty_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    base_branch = "feature/api"

    responses = [
        {
            "args": ["status", "--porcelain"],
            "rc": 0,
            "stdout": " M docs/example.md\n",
            "stderr": "",
        },
        {"args": ["checkout", base_branch], "rc": 0, "stdout": "", "stderr": ""},
        {"args": ["pull", "--ff-only"], "rc": 0, "stdout": "", "stderr": ""},
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
    monkeypatch.setattr(codex_watcher, "ensure_repo_cloned", _fake_ensure_repo_cloned)
    logger = logging.getLogger("test")

    ok = codex_watcher.ensure_worker_repo_clean_and_synced(
        repo, base_branch, logger
    )

    assert ok is True
    assert calls == expected_calls


def test_ensure_worker_repo_clean_and_synced_clean_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    base_branch = "feature/api"

    responses = [
        {"args": ["status", "--porcelain"], "rc": 0, "stdout": "", "stderr": ""},
        {"args": ["checkout", base_branch], "rc": 0, "stdout": "", "stderr": ""},
        {"args": ["pull", "--ff-only"], "rc": 0, "stdout": "", "stderr": ""},
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
    monkeypatch.setattr(codex_watcher, "ensure_repo_cloned", _fake_ensure_repo_cloned)
    logger = logging.getLogger("test")

    ok = codex_watcher.ensure_worker_repo_clean_and_synced(
        repo, base_branch, logger
    )

    assert ok is True
    assert calls == expected_calls


def test_ensure_worker_repo_clean_and_synced_status_failure(tmp_path, monkeypatch, caplog):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    base_branch = "feature/api"

    responses = [
        {"args": ["status", "--porcelain"], "rc": 1, "stdout": "", "stderr": "nope"},
    ]

    def fake_run_git(args, *, cwd, logger, check=False):
        resp = responses.pop(0)

        class P:
            returncode = resp["rc"]
            stdout = resp.get("stdout", "")
            stderr = resp.get("stderr", "")

        return P()

    monkeypatch.setattr(codex_watcher, "_run_git", fake_run_git)
    monkeypatch.setattr(codex_watcher, "ensure_repo_cloned", _fake_ensure_repo_cloned)
    logger = logging.getLogger("test")

    with caplog.at_level(logging.ERROR):
        ok = codex_watcher.ensure_worker_repo_clean_and_synced(
            repo, base_branch, logger
        )

    assert ok is False
    assert "git status" in caplog.text
    assert "Git preflight" in caplog.text
