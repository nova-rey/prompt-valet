from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import rebuild_inbox_tree  # noqa: E402


def _run_git(args: list[str], cwd: Path | None = None) -> None:
    subprocess.run(args, cwd=str(cwd) if cwd else None, check=True)


def _write_config(
    path: Path,
    inbox: Path,
    repos_root: Path,
    remote_root: Path,
    *,
    owner: str = "owner",
    host: Path | str | None = None,
    protocol: str = "file",
) -> Path:
    host_value = host or remote_root
    config_text = textwrap.dedent(
        f"""
        inbox: "{inbox}"
        processed: "{path.parent / 'processed'}"
        repos_root: "{repos_root}"

        watcher:
          git_default_owner: "{owner}"
          git_default_host: "{host_value}"
          git_protocol: "{protocol}"

        tree_builder:
          branch_mode: "all"
          branch_whitelist: []
          branch_blacklist: []
          branch_name_blacklist:
            - "HEAD"
        """
    ).strip()
    path.write_text(config_text, encoding="utf-8")
    return path


def _configure_paths(
    monkeypatch: pytest.MonkeyPatch,
    config_path: Path,
    inbox: Path,
    repos_root: Path,
) -> None:
    monkeypatch.setattr(rebuild_inbox_tree, "INBOX_ROOT", inbox)
    monkeypatch.setattr(rebuild_inbox_tree, "REPOS_ROOT", repos_root)
    monkeypatch.setattr(rebuild_inbox_tree, "DEFAULT_CONFIG_PATH", config_path)


@pytest.fixture
def remote_root(tmp_path: Path) -> Path:
    root = tmp_path / "remote"
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_upstream_repo_with_zero_heads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, remote_root: Path
) -> None:
    inbox = tmp_path / "inbox"
    repos_root = tmp_path / "repos"
    branchless_remote = remote_root / "owner" / "branchless.git"
    branchless_remote.parent.mkdir(parents=True, exist_ok=True)
    _run_git(["git", "init", "--bare", str(branchless_remote)])
    (inbox / "branchless").mkdir(parents=True, exist_ok=True)

    config_path = _write_config(
        tmp_path / "prompt-valet.yaml", inbox, repos_root, remote_root
    )
    _configure_paths(monkeypatch, config_path, inbox, repos_root)

    rebuild_inbox_tree.main()

    repo_root = inbox / "branchless"
    assert (
        repo_root.exists()
    ), "Inbox root should be retained for branchless upstream repo"
    branch_dirs = [p for p in repo_root.iterdir() if p.is_dir()]
    assert (
        not branch_dirs
    ), "No branch directories should be created when upstream has zero heads"


def test_upstream_repo_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, remote_root: Path
) -> None:
    inbox = tmp_path / "inbox"
    repos_root = tmp_path / "repos"
    missing_repo_root = inbox / "missing-repo"
    missing_repo_root.mkdir(parents=True, exist_ok=True)

    config_path = _write_config(
        tmp_path / "prompt-valet.yaml", inbox, repos_root, remote_root
    )
    _configure_paths(monkeypatch, config_path, inbox, repos_root)

    rebuild_inbox_tree.main()

    assert (
        missing_repo_root.exists()
    ), "Missing upstream repo should keep its inbox root"
    error_marker = missing_repo_root / "ERROR.md"
    assert (
        not error_marker.exists()
    ), "Missing upstream repo should not be marked invalid with the new logic"


def test_upstream_heads_create_branch_dirs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, remote_root: Path
) -> None:
    inbox = tmp_path / "inbox"
    repos_root = tmp_path / "repos"
    (inbox / "multi").mkdir(parents=True, exist_ok=True)

    remote_repo = remote_root / "owner" / "multi.git"
    remote_repo.parent.mkdir(parents=True, exist_ok=True)
    _run_git(["git", "init", "--bare", str(remote_repo)])

    worktree = tmp_path / "worktree"
    worktree.mkdir(parents=True, exist_ok=True)
    _run_git(["git", "init"], cwd=worktree)
    _run_git(["git", "config", "user.email", "ci@example.invalid"], cwd=worktree)
    _run_git(["git", "config", "user.name", "Prompt Valet CI"], cwd=worktree)
    (worktree / "README.md").write_text("# demo\n", encoding="utf-8")
    _run_git(["git", "add", "README.md"], cwd=worktree)
    _run_git(["git", "commit", "-m", "init"], cwd=worktree)
    _run_git(["git", "branch", "-M", "main"], cwd=worktree)
    _run_git(["git", "remote", "add", "origin", str(remote_repo)], cwd=worktree)
    _run_git(["git", "push", "-u", "origin", "main"], cwd=worktree)
    _run_git(["git", "checkout", "-b", "feature"], cwd=worktree)
    (worktree / "README.md").write_text("# demo feature\n", encoding="utf-8")
    _run_git(["git", "commit", "-am", "feature"], cwd=worktree)
    _run_git(["git", "push", "-u", "origin", "feature"], cwd=worktree)

    config_path = _write_config(
        tmp_path / "prompt-valet.yaml", inbox, repos_root, remote_root
    )
    _configure_paths(monkeypatch, config_path, inbox, repos_root)

    rebuild_inbox_tree.main()

    repo_root = inbox / "multi"
    expected = {"main", "feature"}
    assert repo_root.is_dir()
    branches = {p.name for p in repo_root.iterdir() if p.is_dir()}
    assert branches == expected


def test_invalid_repo_key_marked_without_deletion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, remote_root: Path
) -> None:
    inbox = tmp_path / "inbox"
    repos_root = tmp_path / "repos"
    invalid_root = inbox / "bad repo"
    invalid_root.mkdir(parents=True, exist_ok=True)

    config_path = _write_config(
        tmp_path / "prompt-valet.yaml", inbox, repos_root, remote_root
    )
    _configure_paths(monkeypatch, config_path, inbox, repos_root)

    rebuild_inbox_tree.main()

    assert (
        invalid_root.exists()
    ), "Spam repo with illegal characters should not be deleted"
    error_marker = invalid_root / "ERROR.md"
    assert error_marker.exists(), "Illegal repo key should produce an error marker"
    assert "illegal characters" in error_marker.read_text(encoding="utf-8")


def test_missing_local_clone_still_processes_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, remote_root: Path
) -> None:
    inbox = tmp_path / "inbox"
    repos_root = tmp_path / "repos"
    repo_key = "nova-process"
    (inbox / repo_key).mkdir(parents=True, exist_ok=True)

    config_path = _write_config(
        tmp_path / "prompt-valet.yaml", inbox, repos_root, remote_root
    )
    _configure_paths(monkeypatch, config_path, inbox, repos_root)

    def fake_ls_remote(
        target: str, heads_only: bool = True, cwd: Path | None = None
    ) -> tuple[bool, list[str], str]:
        return True, ["main"], ""

    monkeypatch.setattr(rebuild_inbox_tree, "run_git_ls_remote", fake_ls_remote)

    rebuild_inbox_tree.main()

    repo_root = inbox / repo_key
    assert (
        repo_root / "main"
    ).is_dir(), "Tree builder should still create branch folders for a valid repo key"
    assert not (
        repo_root / "ERROR.md"
    ).exists(), "Valid repo key should not leave an error marker"


def test_missing_config_runs_local_only_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    inbox = tmp_path / "inbox"
    repos_root = tmp_path / "repos"
    repo_key = "nova-process"
    (inbox / repo_key).mkdir(parents=True, exist_ok=True)

    config_path = tmp_path / "prompt-valet.yaml"
    _configure_paths(monkeypatch, config_path, inbox, repos_root)

    def fail_upstream(
        target: str, heads_only: bool = True, cwd: Path | None = None
    ) -> tuple[bool, list[str], str]:
        if cwd is None:
            raise AssertionError(
                "run_git_ls_remote should not be called when upstream is disabled."
            )
        return True, ["main"], ""

    monkeypatch.setattr(rebuild_inbox_tree, "run_git_ls_remote", fail_upstream)

    rebuild_inbox_tree.main()

    output = capsys.readouterr().out
    assert (
        "upstream discovery disabled; running in local-only mode." in output
    ), "Expected a warning about the disabled upstream when config is absent"
    assert (inbox / repo_key).exists()
    assert not (inbox / repo_key / "ERROR.md").exists()


def test_upstream_error_does_not_invalidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    remote_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    inbox = tmp_path / "inbox"
    repos_root = tmp_path / "repos"
    repo_key = "nova-process"
    (inbox / repo_key).mkdir(parents=True, exist_ok=True)

    config_path = _write_config(
        tmp_path / "prompt-valet.yaml", inbox, repos_root, remote_root
    )
    _configure_paths(monkeypatch, config_path, inbox, repos_root)

    def failing_ls_remote(
        target: str, heads_only: bool = True, cwd: Path | None = None
    ) -> tuple[bool, list[str], str]:
        return False, [], "fatal: unable to access 'origin'"

    monkeypatch.setattr(rebuild_inbox_tree, "run_git_ls_remote", failing_ls_remote)

    rebuild_inbox_tree.main()

    output = capsys.readouterr().out
    assert (
        "Upstream check failed" in output
    ), "Failures should be logged but not treated as invalid"
    assert not (inbox / repo_key / "ERROR.md").exists()


def test_upstream_enabled_uses_config_owner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, remote_root: Path
) -> None:
    inbox = tmp_path / "inbox"
    repos_root = tmp_path / "repos"
    repo_key = "nova-process"
    (inbox / repo_key).mkdir(parents=True, exist_ok=True)

    owner = "custom-owner"
    config_path = _write_config(
        tmp_path / "prompt-valet.yaml",
        inbox,
        repos_root,
        remote_root,
        owner=owner,
    )
    _configure_paths(monkeypatch, config_path, inbox, repos_root)

    targets: list[str] = []

    def capturing_ls_remote(
        target: str, heads_only: bool = True, cwd: Path | None = None
    ) -> tuple[bool, list[str], str]:
        targets.append(target)
        return True, ["main"], ""

    monkeypatch.setattr(rebuild_inbox_tree, "run_git_ls_remote", capturing_ls_remote)

    rebuild_inbox_tree.main()

    assert targets, "Upstream discovery should be attempted when config exists"
    assert any(f"/{owner}/" in target for target in targets)
