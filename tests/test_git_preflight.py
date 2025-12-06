from pathlib import Path
import logging
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import codex_watcher


def test_ensure_repo_clean_and_synced_dirty_repo(tmp_path, monkeypatch, caplog):
    repo = tmp_path / "repo"
    repo.mkdir()

    calls = []

    def fake_run_git(args, *, cwd, logger, check=False):
        calls.append(list(args))

        class P:
            returncode = 0
            stdout = " M docs/example.md\n"
            stderr = ""

        return P()

    monkeypatch.setattr(codex_watcher, "_run_git", fake_run_git)
    logger = logging.getLogger("test")

    with caplog.at_level(logging.ERROR):
        ok = codex_watcher.ensure_repo_clean_and_synced(repo, logger)

    assert ok is False
    assert "Refusing to run Codex: repository" in caplog.text


def test_ensure_repo_clean_and_synced_happy_path(tmp_path, monkeypatch, caplog):
    repo = tmp_path / "repo"
    repo.mkdir()

    calls = []

    def fake_run_git(args, *, cwd, logger, check=False):
        calls.append(list(args))

        class P:
            returncode = 0
            stdout = ""
            stderr = ""

        return P()

    monkeypatch.setattr(codex_watcher, "_run_git", fake_run_git)
    logger = logging.getLogger("test")

    with caplog.at_level(logging.INFO):
        ok = codex_watcher.ensure_repo_clean_and_synced(repo, logger)

    assert ok is True
    assert ["status", "--porcelain"] in calls
    assert ["fetch", "origin"] in calls
    assert ["pull", "--ff-only"] in calls
