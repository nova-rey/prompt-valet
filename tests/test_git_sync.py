import os
import subprocess


def test_git_sync_runs_cleanly():
    repo_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    out = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    assert out.stdout.strip() == b"", "Expected clean working tree after sync."
