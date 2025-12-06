from pathlib import Path

from scripts import codex_watcher


def test_git_sync_uses_configured_repo_path():
    # The repo root where tests are running should be a Git repo.
    repo_root = Path(__file__).resolve().parents[1]
    assert (repo_root / ".git").is_dir(), "Test must run inside a Git clone."

    # Run sync against this repo; should not raise.
    codex_watcher.run_git_sync(str(repo_root))


def test_git_sync_rejects_non_git_directory(tmp_path):
    # Create a temp directory with no .git; sync must fail.
    non_git_dir = tmp_path / "not_a_repo"
    non_git_dir.mkdir()

    try:
        codex_watcher.run_git_sync(str(non_git_dir))
    except RuntimeError as exc:
        # We expect this to fail with a clear message.
        assert "not a Git repository" in str(exc) or "Git synchronization failed" in str(exc)
    else:
        raise AssertionError("Expected run_git_sync to fail on a non-git directory.")
