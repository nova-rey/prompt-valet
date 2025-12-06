#!/usr/bin/env python3
"""
rebuild_inbox_tree.py

Rebuilds the Codex inbox tree under /srv/prompt-valet/inbox.

Behavior (one-shot sync):

1) For each real git repo under /srv/repos:
   - Discover remote branches.
   - Filter them according to tree_builder.branch_mode /
     branch_whitelist / branch_blacklist.
   - Create inbox subdirectories for each "wanted" branch:
         /srv/prompt-valet/inbox/<repo_key>/<branch>/

2) For every existing inbox branch directory under
       /srv/prompt-valet/inbox/<repo_key>/<branch>/
   that does NOT correspond to a real git branch name:
       - Delete the entire branch directory.
       - Write an ERROR.md marker explaining what happened.

3) For every top-level inbox root under /srv/prompt-valet/inbox
   that does NOT correspond to a real git repo in /srv/repos:
       - Delete the entire inbox root.
       - Recreate it with a single ERROR.md explaining that the
         repo key is unknown.

This keeps the inbox tree tightly aligned with real repos/branches
and aggressively cleans up stale or typo'd folders.

A background systemd timer can run this periodically to provide
"eventual consistency" for branch lifecycles.

Config (YAML):

    /srv/prompt-valet/config/prompt-valet.yaml

    tree_builder:
      # If true, also create an empty inbox root for every repo
      # discovered under /srv/repos, even if the user has never
      # touched that repo via Copyparty yet.
      eager_repos: false

      # Branch selection strategy: "all", "whitelist", "blacklist"
      branch_mode: "all"

      # Only used when branch_mode == "whitelist"
      branch_whitelist:
        - "main"

      # Only used when branch_mode == "blacklist"
      branch_blacklist:
        - "agent/"
        - "feature/"
        - "dependabot/"

      # Optional: names that should never be treated as real branches
      # even if git reports them (e.g., "HEAD")
      branch_name_blacklist:
        - "HEAD"

      # How often the systemd timer should re-run this script (seconds).
      # This value is informational here; systemd owns the actual cadence.
      scan_interval_seconds: 60
"""

from __future__ import annotations

import copy
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, Dict, Any, List

import yaml

# Filesystem layout
INBOX_ROOT = Path("/srv/prompt-valet/inbox")
REPOS_ROOT = Path("/srv/repos")
DEFAULT_CONFIG_PATH = Path("/srv/prompt-valet/config/prompt-valet.yaml")


def log(msg: str) -> None:
    print(f"[rebuild_inbox_tree] {msg}", flush=True)


# --- Config loading (YAML) --------------------------------------------------


DEFAULT_CONFIG: Dict[str, Any] = {
    "inbox": str(INBOX_ROOT),
    "processed": "/srv/prompt-valet/processed",
    "repos_root": str(REPOS_ROOT),
    "tree_builder": {
        "eager_repos": False,
        "branch_mode": "all",  # "all" | "whitelist" | "blacklist"
        "branch_whitelist": [],
        "branch_blacklist": [],
        "branch_name_blacklist": ["HEAD"],
        "placeholder_branches": ["main", "devel", "api", "phase5"],  # legacy hint only
        "greedy_inboxes": False,  # legacy hint only
        "scan_interval_seconds": 60,
    },
    "watcher": {
        "auto_clone_missing_repos": True,
        "git_default_owner": "owner",
        "git_default_host": "github.com",
        "git_protocol": "https",
        "cleanup_non_git_dirs": True,
        "runner_cmd": "codex",
        "runner_model": "gpt-5.1-codex-mini",
        "runner_sandbox": "danger-full-access",
    },
}


def load_config() -> Dict[str, Any]:
    """
    Load configuration from DEFAULT_CONFIG_PATH, merging into DEFAULT_CONFIG.

    Missing file or parse failure fall back to DEFAULT_CONFIG.
    """
    cfg: Dict[str, Any] = copy.deepcopy(DEFAULT_CONFIG)
    path = DEFAULT_CONFIG_PATH
    loaded_path = "<defaults>"

    if not path.is_file():
        log(f"No config file at {path}, using defaults.")
    else:
        try:
            user_cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(user_cfg, dict):
                raise ValueError("YAML config is not a mapping at the top level.")

            for section, values in user_cfg.items():
                if isinstance(values, dict) and isinstance(cfg.get(section), dict):
                    cfg[section].update(values)
                else:
                    cfg[section] = values

            loaded_path = str(path)
        except Exception as exc:
            log(f"Failed to read config file {path}: {exc}; using defaults.")

    watcher_cfg = cfg.get("watcher", {})
    log(
        "[prompt-valet] loaded config="
        f"{loaded_path} "
        f"inbox={INBOX_ROOT} "
        "processed=<n/a> "
        f"git_owner={watcher_cfg.get('git_default_owner')} "
        f"git_host={watcher_cfg.get('git_default_host')} "
        f"git_protocol={watcher_cfg.get('git_protocol')} "
        "runner=<none>"
    )

    return cfg


# --- Git / inbox helpers ----------------------------------------------------


def is_git_repo(path: Path) -> bool:
    return (path / ".git").is_dir()


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


def list_remote_branches(repo_path: Path) -> List[str]:
    """
    Return a list of remote branch names (without 'origin/' prefix).
    """
    try:
        result = subprocess.run(
            ["git", "branch", "-r", "--format", "%(refname:short)"],
            cwd=str(repo_path),
            text=True,
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        log(f"Failed to list remote branches for {repo_path}: {e}")
        return []

    branches: List[str] = []
    for line in result.stdout.splitlines():
        name = line.strip()
        if not name:
            continue
        # Typically looks like "origin/main"; strip the "origin/" prefix if present.
        if name.startswith("origin/"):
            name = name[len("origin/") :]
        branches.append(name)
    return branches


def filter_branches_for_inbox(
    branches: List[str],
    *,
    branch_mode: str,
    whitelist: List[str],
    blacklist: List[str],
    name_blacklist: List[str],
) -> List[str]:
    """
    Apply config-driven filters and path-safety rules to decide which
    branch names should get an inbox directory.
    """
    # Drop any explicitly blacklisted-by-name branches (e.g., HEAD)
    filtered: List[str] = []
    for br in branches:
        if br in name_blacklist:
            log(f"  Dropping branch {br!r} (name_blacklist)")
            continue
        filtered.append(br)
    branches = filtered

    # Branch mode filters
    if branch_mode == "whitelist":
        wl = set(whitelist)
        branches = [b for b in branches if b in wl]
    elif branch_mode == "blacklist":
        # blacklist entries are treated as prefixes
        bl_prefixes = tuple(blacklist)
        branches = [b for b in branches if not b.startswith(bl_prefixes)]
    else:
        # "all" -> no additional filtering
        pass

    # Path safety: branches that contain '/' can lead to weird nested
    # directories or collisions. Unless explicitly whitelisted by name,
    # we drop them and ask users to create a cleaner alias branch if
    # they want inbox folders for them.
    safe: List[str] = []
    wl_set = set(whitelist)
    for br in branches:
        if "/" in br and br not in wl_set:
            log(
                f"  Dropping branch {br!r} (contains '/' and is not "
                f"whitelisted; path-unsafe for inbox layout)"
            )
            continue
        safe.append(br)

    return sorted(safe)


def ensure_inbox_dir(repo_key: str, branch: str) -> Path:
    inbox_dir = INBOX_ROOT / repo_key / branch
    inbox_dir.mkdir(parents=True, exist_ok=True)
    log(f"Ensured inbox dir: {inbox_dir}")
    return inbox_dir


def remove_inbox_dir(path: Path, reason: str) -> None:
    """
    Remove an inbox branch directory and write an ERROR.md explaining why.
    """
    if not path.is_dir():
        return

    log(f"Inbox branch {path} has no matching git branch; resetting and dropping ERROR.md.")
    shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)

    error_path = path / "ERROR.md"
    error_text = (
        "# Invalid inbox branch\n\n"
        f"This folder did not correspond to any real git branch.\n\n"
        f"Reason: {reason}\n\n"
        "The Prompt Valet tree builder automatically removed it.\n"
        "If you believe this is an error, check your repo's branch names "
        "and the tree_builder configuration.\n"
    )
    error_path.write_text(error_text, encoding="utf-8")
    log(f"Wrote error marker: {error_path}")


def remove_inbox_root(path: Path, reason: str) -> None:
    """
    Remove an entire inbox repo root that does not map to a real repo.
    """
    if not path.is_dir():
        return

    log(f"Inbox root {path.name!r} has no real repo; resetting and dropping ERROR.md.")
    shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)

    error_path = path / "ERROR.md"
    error_text = (
        "# Invalid repo key\n\n"
        f"This folder did not correspond to any real git repo under {REPOS_ROOT}.\n\n"
        f"Reason: {reason}\n\n"
        "The Prompt Valet tree builder automatically removed it.\n"
        "If you believe this is an error, check your repo names and the "
        "tree_builder configuration.\n"
    )
    error_path.write_text(error_text, encoding="utf-8")
    log(f"Wrote error marker: {error_path}")


def has_real_repo(repo_key: str) -> bool:
    """
    Return True if /srv/repos/<repo_key> exists and looks like a git repo.
    """
    repo_path = REPOS_ROOT / repo_key
    return repo_path.is_dir() and is_git_repo(repo_path)


def build_from_local_repos(cfg: Dict[str, Any]) -> None:
    """
    Walk /srv/repos, inspect real git repos, and create inbox dirs
    for filtered remote branches.
    """
    tb_cfg = cfg.get("tree_builder", {})
    branch_mode = str(tb_cfg.get("branch_mode", "all")).lower()
    branch_whitelist = list(tb_cfg.get("branch_whitelist", []))
    branch_blacklist = list(tb_cfg.get("branch_blacklist", []))
    branch_name_blacklist = list(tb_cfg.get("branch_name_blacklist", ["HEAD"]))

    log(f"branch_mode        = {branch_mode!r}")
    log(f"branch_whitelist   = {branch_whitelist}")
    log(f"branch_blacklist   = {branch_blacklist}")
    log(f"branch_name_blacklist = {branch_name_blacklist}")

    for repo_path in discover_repos(REPOS_ROOT):
        repo_key = repo_path.name
        log(f"Processing repo {repo_key}")

        branches = list_remote_branches(repo_path)
        if not branches:
            log(f"  No remote branches found for {repo_key}, skipping.")
            continue

        wanted = filter_branches_for_inbox(
            branches,
            branch_mode=branch_mode,
            whitelist=branch_whitelist,
            blacklist=branch_blacklist,
            name_blacklist=branch_name_blacklist,
        )
        log(f"  Wanted branches after filtering: {wanted}")

        for br in wanted:
            ensure_inbox_dir(repo_key, br)


def clean_inbox_branches(cfg: Dict[str, Any]) -> None:
    """
    For each inbox repo root that maps to a real repo, ensure that only
    real branches exist as children. Anything else gets nuked and tagged
    with ERROR.md.
    """
    tb_cfg = cfg.get("tree_builder", {})
    branch_mode = str(tb_cfg.get("branch_mode", "all")).lower()
    branch_whitelist = list(tb_cfg.get("branch_whitelist", []))
    branch_blacklist = list(tb_cfg.get("branch_blacklist", []))
    branch_name_blacklist = list(tb_cfg.get("branch_name_blacklist", ["HEAD"]))

    for inbox_repo_root in sorted(INBOX_ROOT.iterdir()) if INBOX_ROOT.is_dir() else []:
        if not inbox_repo_root.is_dir():
            continue
        repo_key = inbox_repo_root.name

        if not has_real_repo(repo_key):
            # This will be handled by clean_inbox_roots()
            continue

        # Get the "correct" branch set from git.
        repo_path = REPOS_ROOT / repo_key
        branches = list_remote_branches(repo_path)
        wanted = set(
            filter_branches_for_inbox(
                branches,
                branch_mode=branch_mode,
                whitelist=branch_whitelist,
                blacklist=branch_blacklist,
                name_blacklist=branch_name_blacklist,
            )
        )

        # Walk existing inbox branches and drop anything not in 'wanted'.
        for child in sorted(inbox_repo_root.iterdir()):
            if not child.is_dir():
                continue
            br = child.name
            if br not in wanted:
                remove_inbox_dir(
                    child,
                    reason=(
                        f"branch name {br!r} does not exist in git for repo {repo_key!r} "
                        f"or was filtered out by tree_builder settings"
                    ),
                )


def clean_inbox_roots() -> None:
    """
    For every top-level inbox root:

        - If there is a matching real repo under /srv/repos/<repo_key>,
          we leave it alone (branch-level cleanup is done elsewhere).

        - If there is NO matching real repo, we nuke the folder and
          drop an ERROR.md.
    """
    if not INBOX_ROOT.is_dir():
        return

    for child in sorted(INBOX_ROOT.iterdir()):
        if not child.is_dir():
            continue
        repo_key = child.name

        if has_real_repo(repo_key):
            continue

        remove_inbox_root(
            child,
            reason=(
                f"repo key {repo_key!r} does not exist as a git repo under {REPOS_ROOT}"
            ),
        )


def main() -> None:
    cfg = load_config()
    tb_cfg = cfg.get("tree_builder", {})
    eager_repos = bool(tb_cfg.get("eager_repos", False))

    log(f"Loaded tree_builder config: {tb_cfg}")
    log(f"Ensuring inbox root exists at {INBOX_ROOT}")
    INBOX_ROOT.mkdir(parents=True, exist_ok=True)

    log("Building from local repos in /srv/repos")
    log(f"branch_mode        = {tb_cfg.get('branch_mode', 'all')!r}")
    log(f"branch_whitelist   = {tb_cfg.get('branch_whitelist', [])}")
    log(f"branch_blacklist   = {tb_cfg.get('branch_blacklist', [])}")
    log(f"branch_name_blacklist = {tb_cfg.get('branch_name_blacklist', ['HEAD'])}")

    build_from_local_repos(cfg)

    if eager_repos:
        # In "eager" mode we also ensure an empty repo root exists
        # for every discovered repo, even if the user hasn't touched
        # it via Copyparty/Prompt Valet yet. This just creates
        # /srv/prompt-valet/inbox/<repo_key>/ with no branches.
        for repo_path in discover_repos(REPOS_ROOT):
            repo_key = repo_path.name
            repo_root = INBOX_ROOT / repo_key
            repo_root.mkdir(parents=True, exist_ok=True)
            log(f"Eager mode: ensured inbox repo root {repo_root}")

    log("Cleaning inbox branches that do not map to real git branches")
    clean_inbox_branches(cfg)

    log("Cleaning inbox roots that do not map to real repos")
    clean_inbox_roots()

    log("Inbox tree rebuild complete.")


if __name__ == "__main__":
    main()
