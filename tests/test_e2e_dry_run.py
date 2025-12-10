from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import codex_watcher, rebuild_inbox_tree


@dataclass
class PromptValetRoot:
    root: Path
    inbox_dir: Path
    processed_dir: Path
    repos_root: Path
    finished_dir: Path
    config_path: Path


@pytest.fixture(name="pv_root")
def fixture_prompt_valet_root(tmp_path: Path) -> PromptValetRoot:
    """Build a temporary Prompt Valet layout with local config."""
    root = tmp_path / "prompt-valet"
    inbox = root / "inbox"
    processed = root / "processed"
    repos = root / "repos"
    finished = root / "finished"
    cfg_dir = root / "config"

    for path in (inbox, processed, repos, finished, cfg_dir):
        path.mkdir(parents=True, exist_ok=True)

    config_path = cfg_dir / "prompt-valet.yaml"
    config_path.write_text(
        textwrap.dedent(
            f"""
            pv_root: "{root}"
            inbox: "{inbox}"
            processed: "{processed}"
            finished: "{finished}"
            repos_root: "{repos}"
            git_owner: "test-owner"

            watcher:
              auto_clone_missing_repos: false
              git_default_owner: "test-owner"
              git_default_host: "example.test"
              git_protocol: "ssh"
              runner_cmd: "dummy-codex"
              runner_model: "gpt-5.1-codex-mini"
              runner_sandbox: "danger-full-access"

            tree_builder:
              branch_mode: "all"
              branch_whitelist: []
              branch_blacklist: []
              branch_name_blacklist:
                - "HEAD"
            """
        ).strip(),
        encoding="utf-8",
    )

    return PromptValetRoot(
        root=root,
        inbox_dir=inbox,
        processed_dir=processed,
        repos_root=repos,
        finished_dir=finished,
        config_path=config_path,
    )


def create_local_repo_with_main_branch(root: Path, name: str = "demo-repo") -> Path:
    """Initialize a git repo with a main branch and an origin remote."""
    repo_root = root / name
    repo_root.mkdir(parents=True, exist_ok=True)

    subprocess.run(["git", "init"], cwd=repo_root, check=True)
    (repo_root / "README.md").write_text("# demo\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo_root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "ci@example.invalid"], cwd=repo_root, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Prompt Valet CI"],
        cwd=repo_root,
        check=True,
    )
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_root, check=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo_root, check=True)

    origin_bare = root / f"{name}-origin.git"
    if origin_bare.exists():
        shutil.rmtree(origin_bare)
    subprocess.run(["git", "init", "--bare", str(origin_bare)], check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", str(origin_bare)], cwd=repo_root, check=True
    )
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=repo_root, check=True)

    return repo_root


def test_dry_run_single_prompt_flows_inbox_to_processed(
    pv_root: PromptValetRoot, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ensure the watcher can claim a prompt, run Codex, and archive processed output."""
    caplog.set_level(logging.INFO, logger="codex_watcher")

    repo_owner_dir = pv_root.repos_root / "test-owner"
    repo = create_local_repo_with_main_branch(repo_owner_dir, "demo-repo")
    repo_inbox = pv_root.inbox_dir / "demo-repo" / "main"
    repo_inbox.mkdir(parents=True, exist_ok=True)
    prompt = repo_inbox / "test-prompt.prompt.md"
    prompt.write_text("# test prompt\n", encoding="utf-8")

    monkeypatch.setattr(
        rebuild_inbox_tree, "INBOX_ROOT", pv_root.inbox_dir
    )
    monkeypatch.setattr(
        rebuild_inbox_tree, "REPOS_ROOT", repo_owner_dir
    )
    monkeypatch.setattr(
        rebuild_inbox_tree, "DEFAULT_CONFIG_PATH", pv_root.config_path
    )
    monkeypatch.setattr(
        codex_watcher, "DEFAULT_CONFIG_PATH", pv_root.config_path
    )
    monkeypatch.setattr(codex_watcher, "DEBOUNCE_SECONDS", 0)

    def fake_run_codex_for_job(repo_dir: Path, job: codex_watcher.Job, run_root: Path) -> None:
        marker = run_root / ".codex-ok"
        marker.write_text("OK", encoding="utf-8")

    monkeypatch.setattr(codex_watcher, "run_codex_for_job", fake_run_codex_for_job)
    monkeypatch.setattr(
        codex_watcher,
        "create_pr_for_job",
        lambda *args, **kwargs: None,
    )

    # tree builder and watcher main() are single-pass entrypoints suitable for tests
    rebuild_inbox_tree.main()
    codex_watcher.main(["--once"])

    assert not prompt.exists(), "prompt should be claimed and removed from inbox"
    processed_branch = (
        pv_root.processed_dir / "test-owner" / "demo-repo" / "main"
    )
    run_dirs = list(processed_branch.iterdir())
    assert run_dirs, "processed directory should contain a run entry"
    prompt_copy = run_dirs[0] / "prompt.md"
    assert prompt_copy.exists(), "processed run should have copied the prompt"
    assert "Git preflight" in caplog.text, "expected watcher log for git preflight"
