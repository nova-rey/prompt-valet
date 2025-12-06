from pathlib import Path
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import codex_watcher


def _create_repo_with_origin(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True)

    worktree = tmp_path / "work"
    subprocess.run(["git", "clone", str(origin), str(worktree)], check=True)

    (worktree / "README.md").write_text("initial\n")
    subprocess.run(["git", "add", "README.md"], cwd=worktree, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=worktree, check=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=worktree, check=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=worktree, check=True)

    return worktree


def test_git_sync_uses_configured_repo_path(tmp_path):
    repo_root = _create_repo_with_origin(tmp_path)

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
