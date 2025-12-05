#!/usr/bin/env python3
"""
codex_watcher.py

Watches the Prompt Valet inbox and runs Codex on any *.prompt.md files.

Layout:

    /srv/prompt-valet/inbox/<repo>/<branch>/<job>.prompt.md

Repo mapping:

    - <repo> maps directly to /srv/repos/<repo>
    - branches must already exist on the remote (we checkout and pull them)

Auto-onboarding:

    - Config file: /etc/codex-runner/config.toml

        [watcher]
        auto_clone_missing_repos = true
        git_default_owner = "nova-rey"   # your real GitHub username
        git_default_host  = "github.com"
        git_protocol      = "https"      # or "ssh"
        cleanup_non_git_dirs = true      # optional safety

    - If a prompt arrives for <repo> and /srv/repos/<repo> does not exist:
        - When auto_clone_missing_repos is true, we git-clone
          via ssh or https into /srv/repos/<repo>.
"""

import os
import secrets
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

INBOX_DIR = "/srv/prompt-valet/inbox"
PROCESSED_DIR = "/srv/prompt-valet/processed"
REPOS_ROOT = "/srv/repos"
CONFIG_PATH = "/etc/codex-runner/config.toml"

# Codex model to use by default
DEFAULT_MODEL = "gpt-5.1-codex-mini"

# Sandbox mode: "read-only", "workspace-write", or "danger-full-access"
DEFAULT_SANDBOX = "danger-full-access"

# Default config if no config file exists or keys are missing.
DEFAULT_CONFIG: Dict[str, Dict[str, Any]] = {
    "tree_builder": {
        "greedy_inboxes": False,
    },
    "watcher": {
        "auto_clone_missing_repos": True,
        "git_default_owner": None,
        "git_default_host": "github.com",
        "git_protocol": "ssh",
        "cleanup_non_git_dirs": False,
    },
}


def log(msg: str) -> None:
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[codex_watcher] [{ts}] {msg}", flush=True)


def run(cmd, cwd=None, env=None, check=True):
    """Thin wrapper around subprocess.run that logs commands."""
    log(f"RUN: {cmd!r} (cwd={cwd})")
    result = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.stdout:
        log(f"STDOUT:\n{result.stdout.strip()}")
    if result.stderr:
        log(f"STDERR:\n{result.stderr.strip()}")
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed with code {result.returncode}: {cmd}")
    return result


# --- Config loading ---------------------------------------------------------


def _load_toml_file(path: Path) -> Dict[str, Any]:
    """Load a TOML file using tomllib or toml."""
    # 1) stdlib tomllib (3.11+)
    try:
        import tomllib  # type: ignore[attr-defined]

        with path.open("rb") as f:
            return tomllib.load(f)
    except Exception:
        pass

    # 2) external 'toml' package
    try:
        import toml  # type: ignore[import]

        with path.open("r", encoding="utf-8") as f:
            return toml.load(f)
    except Exception:
        pass

    raise RuntimeError("No TOML parser available (need Python 3.11+ or install 'toml').")


def load_config() -> Dict[str, Dict[str, Any]]:
    cfg: Dict[str, Dict[str, Any]] = {k: v.copy() for k, v in DEFAULT_CONFIG.items()}
    cfg_path = Path(CONFIG_PATH)

    if not cfg_path.is_file():
        log(f"No config file at {CONFIG_PATH}, using defaults.")
        return cfg

    try:
        user_cfg = _load_toml_file(cfg_path)
    except Exception as e:
        log(f"Failed to read config file {CONFIG_PATH}: {e}; using defaults.")
        return cfg

    for section, values in user_cfg.items():
        if section in cfg and isinstance(values, dict):
            for key, value in values.items():
                cfg[section][key] = value

    return cfg


WATCHER_CONFIG = load_config().get("watcher", {})
AUTO_CLONE = bool(WATCHER_CONFIG.get("auto_clone_missing_repos", True))
GIT_DEFAULT_OWNER = WATCHER_CONFIG.get("git_default_owner") or None
GIT_DEFAULT_HOST = WATCHER_CONFIG.get("git_default_host") or "github.com"
GIT_PROTOCOL = WATCHER_CONFIG.get("git_protocol", "ssh")
CLEANUP_NON_GIT_DIRS = bool(WATCHER_CONFIG.get("cleanup_non_git_dirs", False))


# --- Path helpers -----------------------------------------------------------


def _split_inbox_path(path: str) -> Optional[tuple[str, str, str]]:
    """
    Given an absolute path under INBOX_DIR, return (repo_key, branch, job_name).

    Example:
        /srv/prompt-valet/inbox/crapssim/main/test.prompt.md
        -> ("crapssim", "main", "test")
    """
    inbox_root = Path(INBOX_DIR)
    p = Path(path)

    try:
        rel = p.relative_to(inbox_root)
    except ValueError:
        log(f"Path {path!r} is not under INBOX_DIR {INBOX_DIR!r}, skipping.")
        return None

    parts = list(rel.parts)
    if len(parts) < 3:
        log(f"Path {path!r} does not look like repo/branch/file, skipping.")
        return None

    repo_key = parts[0]
    branch = parts[1]
    filename = parts[-1]

    if not filename.endswith(".prompt.md"):
        log(f"File {filename!r} does not end with .prompt.md, skipping.")
        return None

    job = filename[: -len(".prompt.md")]
    return repo_key, branch, job


def _build_remote_url(repo_key: str) -> Optional[str]:
    """
    Build the git remote URL based on protocol and config.
    """
    if not GIT_DEFAULT_OWNER:
        log(
            "auto_clone_missing_repos is true but git_default_owner is not set; "
            "cannot construct clone URL."
        )
        return None

    if GIT_PROTOCOL == "https":
        # Anonymous HTTPS; fine for public repos, and git will use your
        # credential helper for pushes if needed.
        return f"https://{GIT_DEFAULT_HOST}/{GIT_DEFAULT_OWNER}/{repo_key}.git"
    else:
        # Default: SSH
        return f"git@{GIT_DEFAULT_HOST}:{GIT_DEFAULT_OWNER}/{repo_key}.git"


def _ensure_repo_present(repo_key: str) -> Optional[str]:
    """
    Ensure /srv/repos/<repo_key> exists and is a real git repo.

    Behavior:
        - If dir does not exist: clone it (when AUTO_CLONE is true).
        - If dir exists and has .git: reuse it.
        - If dir exists but has no .git:
            - if CLEANUP_NON_GIT_DIRS: remove and treat as missing (then clone)
            - else: log and bail.
    """
    repo_path = Path(REPOS_ROOT) / repo_key

    if repo_path.is_dir():
        git_dir = repo_path / ".git"
        if git_dir.is_dir():
            # Real git repo, we're good.
            return str(repo_path)

        # Exists but not a git repo: "ghost" directory case.
        if CLEANUP_NON_GIT_DIRS:
            log(
                f"{repo_path} exists but is not a git repo; "
                "removing it because cleanup_non_git_dirs=true."
            )
            shutil.rmtree(repo_path)
        else:
            log(
                f"{repo_path} exists but is not a git repo; "
                "refusing to delete automatically. "
                "Set watcher.cleanup_non_git_dirs=true to enable auto-cleanup."
            )
            return None

    # At this point, repo_path definitely does not exist.
    log(f"Repo path does not exist: {repo_path}")

    if not AUTO_CLONE:
        log("auto_clone_missing_repos is false; not attempting clone.")
        return None

    remote = _build_remote_url(repo_key)
    if not remote:
        return None

    log(f"Attempting to auto-clone missing repo from {remote!r} into {repo_path}")
    repo_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        run(["git", "clone", remote, str(repo_path)], cwd=str(repo_path.parent))
    except RuntimeError as e:
        log(f"Auto-clone failed for {repo_key}: {e}")
        return None

    if repo_path.is_dir() and (repo_path / ".git").is_dir():
        log(f"Auto-clone succeeded for repo {repo_key}, path {repo_path}")
        return str(repo_path)

    log(f"Repo directory {repo_path} still missing or not a git repo after clone attempt.")
    return None


# --- Core processing --------------------------------------------------------


def process_prompt_file(path: str) -> None:
    log(f"Detected new prompt file: {path}")

    split = _split_inbox_path(path)
    if split is None:
        return

    repo_key, branch, job = split
    filename = os.path.basename(path)

    repo_path = _ensure_repo_present(repo_key)
    if repo_path is None:
        log(f"Cannot process {filename}: repo {repo_key!r} is unavailable.")
        return

    # Read prompt content
    with open(path, "r", encoding="utf-8") as f:
        prompt_content = f.read().strip()

    if not prompt_content:
        log(f"Prompt file is empty: {filename}, skipping.")
        return

    log(f"Processing job '{job}' for repo '{repo_key}' branch '{branch}'")

    # Git dance: checkout branch + pull latest
    run(["git", "fetch", "origin"], cwd=repo_path)
    try:
        run(["git", "checkout", branch], cwd=repo_path)
    except RuntimeError as e:
        log(f"Failed to checkout branch {branch!r} in {repo_key}: {e}")
        return

    run(["git", "pull", "origin", branch], cwd=repo_path)

    # Create a new branch for this agent run
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M")  # e.g. 20251204-1336
    suffix = secrets.token_hex(2)                   # tiny collision-avoid suffix
    branch_name = f"agent/{job}-{ts}-{suffix}"
    run(["git", "checkout", "-b", branch_name], cwd=repo_path)

    # Ensure docs/AGENT_RUNS exists
    agent_runs_dir = os.path.join(repo_path, "docs", "AGENT_RUNS")
    os.makedirs(agent_runs_dir, exist_ok=True)
    output_file = os.path.join(agent_runs_dir, f"codex-run-{ts}-{suffix}.md")

    env = os.environ.copy()
    model = DEFAULT_MODEL

    cmd = [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--cd",
        repo_path,
        "--output-last-message",
        output_file,
        "--model",
        model,
        "--sandbox",
        DEFAULT_SANDBOX,
        prompt_content,
    ]
    run(cmd, env=env)

    # Show status and commit whatever changed
    run(["git", "status"], cwd=repo_path, check=False)
    run(["git", "add", "-A"], cwd=repo_path)
    run(["git", "commit", "-m", f"Codex agent run: {job}"], cwd=repo_path)

    # Push branch
    run(["git", "push", "origin", branch_name], cwd=repo_path)

    # Create PR via gh (if available)
    try:
        pr_title = f"Codex agent run: {job}"
        pr_body = (
            f"Codex agent run for job `{job}`.\n\n"
            f"- Repo: `{repo_key}`\n"
            f"- Branch: `{branch_name}` (base `{branch}`)\n"
            f"- Report file: `{os.path.relpath(output_file, repo_path)}`\n"
        )
        cmd_gh = [
            "gh",
            "pr",
            "create",
            "--base",
            branch,
            "--head",
            branch_name,
            "--title",
            pr_title,
            "--body",
            pr_body,
        ]
        run(cmd_gh, cwd=repo_path)
    except Exception as e:
        log(f"Warning: failed to create PR with gh: {e}")

    # Move processed prompt file
    os.makedirs(PROCESSED_DIR, exist_ok=True)
    dest = os.path.join(PROCESSED_DIR, filename)
    shutil.move(path, dest)
    log(f"Moved processed file to {dest}")
    log(f"Job '{job}' complete.")


class PromptHandler(FileSystemEventHandler):
    def _maybe_process(self, path: str) -> None:
        if not path.endswith(".prompt.md"):
            return
        time.sleep(1.0)  # let uploads finish
        try:
            process_prompt_file(path)
        except Exception as e:
            log(f"Error processing {path}: {e}")

    def on_created(self, event):
        if event.is_directory:
            return
        log(f"on_created event for {event.src_path}")
        self._maybe_process(event.src_path)

    def on_moved(self, event):
        if event.is_directory:
            return
        log(f"on_moved event from {event.src_path} to {event.dest_path}")
        self._maybe_process(event.dest_path)


def main():
    log(f"Starting Codex watcher on {INBOX_DIR}")
    os.makedirs(INBOX_DIR, exist_ok=True)
    os.makedirs(PROCESSED_DIR, exist_ok=True)

    event_handler = PromptHandler()
    observer = Observer()
    observer.schedule(event_handler, INBOX_DIR, recursive=True)
    log(f"Observer scheduled on {INBOX_DIR} (recursive=True)")
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("Stopping watcher...")
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()