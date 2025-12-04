#!/usr/bin/env python3
"""
rebuild_inbox_tree.py

Rebuilds the Codex inbox tree under /srv/copyparty/inbox.

Behavior (one-shot sync):

1) For each real git repo under /srv/repos:
   - Discover remote branches.
   - Filter them according to tree_builder.branch_mode / whitelist / blacklist.
   - Drop path-unsafe names (e.g. containing '/') unless explicitly whitelisted.
   - Ensure inbox dirs exist:

       /srv/copyparty/inbox/<repo>/<branch>/

2) For each inbox repo root under /srv/copyparty/inbox:
   - If there is NO matching git repo under /srv/repos/<repo>:
       * Nuke the entire folder and recreate with an ERROR.md explaining
         that the repo does not exist.

   - If there IS a matching git repo:
       * For each branch folder under that inbox root:
           - If name == "HEAD":
               · Delete the folder outright (no ERROR.md).
           - Else if the name is NOT one of the valid git branches:
               · Delete the folder contents and recreate the folder with
                 an ERROR.md explaining that the branch does not exist in git.

Config file (YAML):

    /srv/copyparty/config/codex-runner.yaml

Example:

    tree_builder:
      eager_repos: false
      branch_mode: "all"        # "all" | "whitelist" | "blacklist"
      branch_whitelist: []      # e.g. ["main", "codex/perform-deep-health-scan-and-verification"]
      branch_blacklist: []      # e.g. ["gh-pages"]
      placeholder_branches:     # currently unused, kept for forward-compat
        - "main"
        - "devel"
        - "api"
        - "phase5"
      greedy_inboxes: false     # reserved for a future "grab all repos" mode
      scan_interval_seconds: 60 # for future loop/daemon use; ignored in this script

Notes on slash branches:

    - Branch names that contain "/" cannot safely map 1:1 into
      /inbox/<repo>/<branch>/ without introducing extra subdirectory levels.
    - By default, any branch containing "/" is DROPPED as path-unsafe.
    - To allow a specific slash-branch, add its full name to branch_whitelist.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Iterable, Dict, Any, List

# Filesystem layout
INBOX_ROOT = Path("/srv/copyparty/inbox")
REPOS_ROOT = Path("/srv/repos")
CONFIG_PATH = Path("/srv/copyparty/config/codex-runner.yaml")

# Default config if no config file exists or keys are missing.
DEFAULT_CONFIG: Dict[str, Dict[str, Any]] = {
    "tree_builder": {
        "eager_repos": False,
        "branch_mode": "all",        # "all" | "whitelist" | "blacklist"
        "branch_whitelist": [],
        "branch_blacklist": [],
        "placeholder_branches": ["main", "devel", "api", "phase5"],
        "greedy_inboxes": False,
        "scan_interval_seconds": 60,
    },
    "watcher": {
        "auto_clone_missing_repos": True,
        "git_default_owner": None,
        "git_default_host": "github.com",
    },
}


def log(msg: str) -> None:
    print(f"[rebuild_inbox_tree] {msg}", flush=True)


# --- Config loading ---------------------------------------------------------


def _load_yaml_file(path: Path) -> Dict[str, Any]:
    """
    Load a YAML config file.

    Requires PyYAML (python3 -m pip install pyyaml).
    """
    try:
        import yaml  # type: ignore[import]
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            f"PyYAML is required to read {path} (pip install pyyaml): {e}"
        )

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise RuntimeError(f"YAML config {path} did not parse as a mapping.")
    return data


def load_config() -> Dict[str, Dict[str, Any]]:
    """
    Load configuration from CONFIG_PATH, shallow-merged over DEFAULT_CONFIG.

    Missing file or parse failure falls back to DEFAULT_CONFIG.
    """
    cfg: Dict[str, Dict[str, Any]] = {k: dict(v) for k, v in DEFAULT_CONFIG.items()}

    if not CONFIG_PATH.is_file():
        log(f"No config file at {CONFIG_PATH}, using defaults.")
        return cfg

    try:
        user_cfg = _load_yaml_file(CONFIG_PATH)
    except Exception as e:
        log(f"Failed to read config file {CONFIG_PATH}: {e}; using defaults.")
        return cfg

    for section, values in user_cfg.items():
        if section in cfg and isinstance(values, dict):
            for key, value in values.items():
                cfg[section][key] = value

    return cfg


# --- Git helpers ------------------------------------------------------------


def is_git_repo(path: Path) -> bool:
    return (path / ".git").is_dir()


def has_real_repo(repo_key: str) -> bool:
    """
    Return True if /srv/repos/<repo_key> exists and looks like a git repo.
    """
    repo_path = REPOS_ROOT / repo_key
    return repo_path.is_dir() and is_git_repo(repo_path)


def discover_repos(root: Path) -> Iterable[Path]:
    """
    Discover git repos directly under REPOS_ROOT.
    """
    if not root.is_dir():
        log(f"Repos root {root} does not exist or is not a directory.")
        return []
    for child in sorted(root.iterdir()):
        if child.is_dir() and is_git_repo(child):
            yield child


def list_remote_heads(repo_path: Path) -> List[str]:
    """
    List remote branches (heads) for the given repo, using:

        git ls-remote --heads origin

    Returns a list of branch names like ["main", "gh-pages", ...].
    """
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--heads", "origin"],
            cwd=str(repo_path),
            text=True,
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        log(f"Failed to list remote heads for {repo_path}: {e}")
        return []

    branches: List[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # Format: "<sha>\trefs/heads/<branch>"
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        ref = parts[1]
        if ref.startswith("refs/heads/"):
            branches.append(ref[len("refs/heads/") :])
    return branches


# --- Branch filtering -------------------------------------------------------


def filter_branches(
    branches: List[str],
    mode: str,
    whitelist: List[str],
    blacklist: List[str],
) -> List[str]:
    """
    Filter the list of branch names according to mode + lists.

    Additionally, drop branches that contain '/' unless they are explicitly
    in the whitelist (path-unsafe for direct folder mapping).
    """
    wanted: List[str] = []

    for b in branches:
        # Always drop obviously path-unsafe branches unless whitelisted.
        if "/" in b and b not in whitelist:
            log(
                f"  Dropping branch {b!r} (contains '/' and is not whitelisted; "
                f"path-unsafe for inbox layout)"
            )
            continue

        if mode == "whitelist":
            if b in whitelist:
                wanted.append(b)
        elif mode == "blacklist":
            if b not in blacklist:
                wanted.append(b)
        else:  # "all"
            wanted.append(b)

    return wanted


# --- Inbox helpers ----------------------------------------------------------


def ensure_inbox_dir(repo_key: str, branch: str) -> Path:
    inbox_dir = INBOX_ROOT / repo_key / branch
    inbox_dir.mkdir(parents=True, exist_ok=True)
    log(f"Ensured inbox dir: {inbox_dir}")
    return inbox_dir


def build_from_local_repos(tree_cfg: Dict[str, Any]) -> Dict[str, List[str]]:
    """
    Walk /srv/repos, inspect real git repos,
    and create inbox dirs for branches that pass the filter.

    Returns a mapping: repo_key -> list of valid branches.

    This is later used to clean up stray/invalid inbox branch folders.
    """
    branch_mode = str(tree_cfg.get("branch_mode", "all")).strip().lower()
    branch_whitelist = list(tree_cfg.get("branch_whitelist", []))
    branch_blacklist = list(tree_cfg.get("branch_blacklist", []))

    log(f"branch_mode        = {branch_mode!r}")
    log(f"branch_whitelist   = {branch_whitelist}")
    log(f"branch_blacklist   = {branch_blacklist}")

    repo_branches: Dict[str, List[str]] = {}

    for repo_path in discover_repos(REPOS_ROOT):
        repo_dir_name = repo_path.name
        repo_key = repo_dir_name  # repo key == folder name
        log(f"Processing repo {repo_dir_name}")

        all_heads = list_remote_heads(repo_path)
        if not all_heads:
            log(f"  No remote heads found for {repo_dir_name}, skipping.")
            continue

        log(f"  Found {len(all_heads)} remote heads for {repo_dir_name}")
        wanted = filter_branches(all_heads, branch_mode, branch_whitelist, branch_blacklist)
        log(f"  Wanted branches after filtering: {wanted}")

        for branch in wanted:
            ensure_inbox_dir(repo_key, branch)

        repo_branches[repo_key] = wanted

    return repo_branches


def clean_inbox_for_real_repos(repo_branches: Dict[str, List[str]]) -> None:
    """
    For each inbox repo root that has a real git repo, ensure that only
    valid branches exist as subfolders.

    Rules per branch folder:
      - If name == "HEAD":
          * Delete the folder entirely (no ERROR.md).
      - Else if not in valid_branches:
          * Delete contents and recreate folder with ERROR.md.
    """
    valid_repo_keys = set(repo_branches.keys())

    if not INBOX_ROOT.is_dir():
        return

    for repo_root in sorted(INBOX_ROOT.iterdir()):
        if not repo_root.is_dir():
            continue
        repo_key = repo_root.name

        if repo_key not in valid_repo_keys:
            # We'll handle non-real repos in a separate pass.
            continue

        valid_branches = set(repo_branches.get(repo_key, []))
        for branch_dir in sorted(repo_root.iterdir()):
            if not branch_dir.is_dir():
                continue
            branch_name = branch_dir.name

            # HEAD is never a real branch in inbox-land; treat as pure junk.
            if branch_name == "HEAD":
                log(
                    f"Inbox branch {branch_dir} is a stray 'HEAD' folder; "
                    f"removing it outright."
                )
                shutil.rmtree(branch_dir, ignore_errors=True)
                continue

            if branch_name not in valid_branches:
                log(
                    f"Inbox branch {branch_dir} has no matching git branch; "
                    f"resetting and dropping ERROR.md."
                )
                # Nuke folder and recreate with ERROR.md
                shutil.rmtree(branch_dir, ignore_errors=True)
                branch_dir.mkdir(parents=True, exist_ok=True)
                error_file = branch_dir / "ERROR.md"
                error_file.write_text(
                    (
                        f"# Invalid inbox branch\n\n"
                        f"This folder was removed because there is no matching git branch "
                        f"named `{branch_name}` in repo `{repo_key}`.\n"
                    ),
                    encoding="utf-8",
                )
                log(f"Wrote error marker: {error_file}")


def clean_inbox_roots_without_repos(repo_branches: Dict[str, List[str]]) -> None:
    """
    For each inbox repo root that does NOT have a matching real git repo,
    nuke it and replace it with a bare folder containing ERROR.md.
    """
    real_repos = set(repo_branches.keys())

    if not INBOX_ROOT.is_dir():
        return

    for repo_root in sorted(INBOX_ROOT.iterdir()):
        if not repo_root.is_dir():
            continue
        repo_key = repo_root.name

        if repo_key in real_repos:
            # Real repo; handled by clean_inbox_for_real_repos.
            continue

        if has_real_repo(repo_key):
            # Repo exists on disk but maybe wasn't discovered for some reason;
            # be conservative and skip auto-nuking in this edge case.
            log(
                f"Inbox root {repo_key!r} appears to have a real repo but was not "
                f"in repo_branches; skipping destructive cleanup."
            )
            continue

        log(
            f"Inbox root {repo_key!r} has no matching git repo; "
            f"removing it and dropping ERROR.md."
        )
        shutil.rmtree(repo_root, ignore_errors=True)
        repo_root.mkdir(parents=True, exist_ok=True)
        error_file = repo_root / "ERROR.md"
        error_file.write_text(
            (
                f"# Invalid inbox repo\n\n"
                f"This inbox root did not correspond to any git repo under "
                f"`{REPOS_ROOT}` (expected `{REPOS_ROOT / repo_key}`).\n"
                f"It was automatically reset.\n"
            ),
            encoding="utf-8",
        )
        log(f"Wrote repo-level error marker: {error_file}")


def main() -> None:
    cfg = load_config()
    tree_cfg = cfg.get("tree_builder", {})
    log(f"Loaded tree_builder config: {tree_cfg}")

    log(f"Ensuring inbox root exists at {INBOX_ROOT}")
    INBOX_ROOT.mkdir(parents=True, exist_ok=True)

    log(f"Building from local repos in {REPOS_ROOT}")
    repo_branches = build_from_local_repos(tree_cfg)

    log("Cleaning inbox branches for real repos")
    clean_inbox_for_real_repos(repo_branches)

    log("Ensuring inbox roots without matching repos are cleaned")
    clean_inbox_roots_without_repos(repo_branches)

    log("Inbox tree rebuild complete.")


if __name__ == "__main__":
    main()