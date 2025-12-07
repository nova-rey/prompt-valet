#!/usr/bin/env python3
"""
rebuild_inbox_tree.py

Rebuilds the Codex inbox tree under /srv/prompt-valet/inbox.

Behavior (one-shot sync):

1) For each git repo under /srv/repos or existing inbox root:
   - Discover remote branches from the repo's origin remote or from
     the upstream host (via git ls-remote).
   - Filter branches according to tree_builder.branch_mode /
     branch_whitelist / branch_blacklist.
   - Create inbox subdirectories for each "wanted" branch:
         /srv/prompt-valet/inbox/<repo_key>/<branch>/
   - Remove inbox branch directories that no longer map to upstream
     branches.

2) For inbox roots without a local clone:
   - Validate the repo against the upstream git host using watcher
     config (owner/host/protocol).
   - If the repo exists upstream, reconcile branch folders against
     upstream heads.
   - If the repo does not exist upstream, delete the inbox root and
     write an ERROR.md marker.

This keeps the inbox tree aligned with real repos/branches while also
respecting repositories that only exist upstream. A background systemd
timer can run this periodically to provide "eventual consistency" for
branch lifecycles.

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
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, Dict, Any, List, Set, Tuple

import json
import urllib.request
from urllib.error import HTTPError, URLError

import yaml

# Filesystem layout
INBOX_ROOT = Path("/srv/prompt-valet/inbox")
REPOS_ROOT = Path("/srv/repos")
DEFAULT_CONFIG_PATH = Path("/srv/prompt-valet/config/prompt-valet.yaml")
CONFIG_ENV_VAR = "PV_CONFIG_PATH"

REPO_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


def is_valid_repo_key(repo_key: str) -> bool:
    return bool(REPO_KEY_PATTERN.fullmatch(repo_key))


def log(msg: str) -> None:
    print(f"[rebuild_inbox_tree] {msg}", flush=True)


# --- Config loading (YAML) --------------------------------------------------


DEFAULT_CONFIG: Dict[str, Any] = {
    "inbox": str(INBOX_ROOT),
    "processed": "/srv/prompt-valet/processed",
    "repos_root": str(REPOS_ROOT),
    "tree_builder": {
        "eager_repos": False,
        "greedy_inboxes": False,
        "branch_mode": "all",  # "all" | "whitelist" | "blacklist"
        "branch_whitelist": [],
        "branch_blacklist": [],
        "branch_name_blacklist": ["HEAD"],
        "scan_interval_seconds": 60,
    },
    "watcher": {
        "auto_clone_missing_repos": True,
        "git_default_owner": "owner",
        "git_default_host": "github.com",
        "git_protocol": "https",
        "git_api_token_env": None,
        "cleanup_non_git_dirs": True,
        "runner_cmd": "codex",
        "runner_model": "gpt-5.1-codex-mini",
        "runner_sandbox": "danger-full-access",
    },
}


def resolve_config_path() -> Path:
    env_path = os.environ.get(CONFIG_ENV_VAR)
    if not env_path:
        return DEFAULT_CONFIG_PATH
    return Path(env_path)


def load_config() -> tuple[Dict[str, Any], bool]:
    """
    Load configuration from DEFAULT_CONFIG_PATH, merging into DEFAULT_CONFIG.

    Missing file or parse failure fall back to DEFAULT_CONFIG.
    """
    cfg: Dict[str, Any] = copy.deepcopy(DEFAULT_CONFIG)
    path = resolve_config_path()
    loaded_path = "<defaults>"
    config_loaded = False

    if not path.is_file():
        log(
            f"No config file at {path}; upstream discovery disabled; running in local-only mode."
        )
    else:
        loaded_path = str(path)
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
            config_loaded = True
        except Exception as exc:
            log(
                f"Failed to read config file {path}: {exc}; "
                "upstream discovery disabled; running in local-only mode."
            )

    watcher_cfg = cfg.get("watcher", {})
    log(
        "[prompt-valet] loaded config="
        f"{loaded_path} "
        f"inbox={INBOX_ROOT} "
        "processed=<n/a> "
        f"git_owner={watcher_cfg.get('git_default_owner')} "
        f"git_host={watcher_cfg.get('git_default_host')} "
        f"git_protocol={watcher_cfg.get('git_protocol')} "
        f"upstream_enabled={config_loaded} "
        "runner=<none>"
    )

    return cfg, config_loaded


# --- Git / inbox helpers ----------------------------------------------------


def is_git_repo(path: Path) -> bool:
    return (path / ".git").is_dir()


def repo_missing_from_stderr(stderr: str) -> bool:
    lowered = (stderr or "").lower()
    tokens = (
        "repository not found",
        "does not appear to be a git repository",
        "could not read from remote repository",
        "repository does not exist",
        "not found",
    )
    return any(tok in lowered for tok in tokens)


def discover_repos(root: Path) -> Iterable[Path]:
    """Discover git repos directly under REPOS_ROOT."""
    if not root.is_dir():
        log(f"Repos root {root} does not exist or is not a directory.")
        return []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if is_git_repo(child):
            yield child
            continue
        for grandchild in sorted(child.iterdir()):
            if grandchild.is_dir() and is_git_repo(grandchild):
                yield grandchild


def _extract_next_link(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        sections = part.split(";")
        if len(sections) < 2:
            continue
        url_part = sections[0].strip()
        rel_part = sections[1].strip()
        if 'rel="next"' not in rel_part:
            continue
        if url_part.startswith("<") and url_part.endswith(">"):
            return url_part[1:-1]
    return None


def _build_github_api_base(host: str | None, protocol: str | None) -> str:
    host = host or "github.com"
    protocol = protocol or "https"
    if host.lower().endswith("github.com"):
        return "https://api.github.com"
    return f"{protocol}://{host.rstrip('/')}/api/v3"


def discover_upstream_repos_for_owner(
    owner: str,
    host: str | None,
    protocol: str | None,
    token_env: str | None = None,
) -> list[str]:
    if not owner:
        return []
    api_base = _build_github_api_base(host, protocol)
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "prompt-valet/greedy-inboxes",
    }
    if token_env:
        token_value = os.getenv(token_env)
        if token_value:
            headers["Authorization"] = f"token {token_value}"
    repos: list[str] = []
    page_url = f"{api_base}/users/{owner}/repos?per_page=100"
    while page_url:
        try:
            request = urllib.request.Request(page_url, headers=headers)
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.load(response)
                if isinstance(payload, list):
                    for repo in payload:
                        if isinstance(repo, dict):
                            name = repo.get("name")
                            if isinstance(name, str):
                                repos.append(name)
                else:
                    log(
                        f"greedy_inboxes=True: unexpected payload shape "
                        f"while discovering repos for owner '{owner}'"
                    )
                page_url = _extract_next_link(response.getheader("Link"))
        except HTTPError as exc:
            log(
                f"greedy_inboxes=True: GitHub API {page_url} responded with "
                f"{exc.code}: {exc.reason}"
            )
            break
        except URLError as exc:
            log(f"greedy_inboxes=True: GitHub API request failed for {page_url}: {exc}")
            break
        except json.JSONDecodeError as exc:
            log(
                f"greedy_inboxes=True: Failed to parse GitHub response for '{owner}': "
                f"{exc}"
            )
            break
        except Exception as exc:  # pragma: no cover - guard against unexpected issues
            log(
                f"greedy_inboxes=True: Unexpected error discovering repos for "
                f"'{owner}': {exc}"
            )
            break
    return repos


def run_git_ls_remote(
    target: str, *, heads_only: bool = True, cwd: Path | None = None
) -> Tuple[bool, List[str], str]:
    args = ["git", "ls-remote"]
    if heads_only:
        args.append("--heads")
    args.append(target)

    try:
        result = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            text=True,
            capture_output=True,
            check=False,
        )
    except Exception as exc:  # pragma: no cover - unexpected failure path
        log(f"git ls-remote failed for {target}: {exc}")
        return False, [], str(exc)

    success = result.returncode == 0
    if not success:
        log(
            "git ls-remote for "
            f"{target} failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    refs: List[str] = []
    if success:
        for line in result.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) < 2:
                continue
            ref = parts[1]
            if heads_only and ref.startswith("refs/heads/"):
                refs.append(ref[len("refs/heads/") :])
            elif heads_only:
                continue
            else:
                refs.append(ref)

    return success, refs, result.stderr


def list_remote_branches(repo_path: Path) -> List[str]:
    """Return a list of remote branch names (without 'origin/' prefix)."""
    success, branches, _ = run_git_ls_remote("origin", heads_only=True, cwd=repo_path)
    if not success:
        log(f"Failed to list remote branches for {repo_path}")
        return []
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
    filtered: List[str] = []
    for br in branches:
        if br in name_blacklist:
            log(f"  Dropping branch {br!r} (name_blacklist)")
            continue
        filtered.append(br)
    branches = filtered

    if branch_mode == "whitelist":
        wl = set(whitelist)
        branches = [b for b in branches if b in wl]
    elif branch_mode == "blacklist":
        bl_prefixes = tuple(blacklist)
        branches = [b for b in branches if not b.startswith(bl_prefixes)]

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
    """Remove an inbox branch directory and write an ERROR.md explaining why."""
    if not path.is_dir():
        return

    log(
        f"Removing inbox branch {path} because it no longer maps to a valid "
        f"upstream branch ({reason})."
    )
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


def _write_repo_error(path: Path, reason: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    error_path = path / "ERROR.md"
    error_text = (
        "# Invalid repo key\n\n"
        "This folder failed the tree builder's repo-key validation rules.\n\n"
        f"Reason: {reason}\n\n"
        "The Prompt Valet tree builder wrote this marker without deleting "
        "the folder. If you believe this is an error, review the repo key "
        "and the `tree_builder` configuration.\n"
    )
    error_path.write_text(error_text, encoding="utf-8")
    log(f"Wrote error marker: {error_path}")
    return error_path


def mark_inbox_root_invalid(path: Path, reason: str) -> None:
    log(f"Marking inbox root {path.name!r} invalid: {reason}")
    _write_repo_error(path, reason)


def mark_inbox_root_valid(path: Path) -> None:
    error_path = path / "ERROR.md"
    if error_path.is_file():
        error_path.unlink()
        log(f"Removed invalid marker: {error_path}")


def build_remote_url(repo_key: str, cfg: Dict[str, Any]) -> str:
    watcher_cfg = cfg.get("watcher", {})
    owner = watcher_cfg.get("git_default_owner", "owner")
    host = watcher_cfg.get("git_default_host", "github.com")
    proto = watcher_cfg.get("git_protocol", "https")
    return f"{proto}://{host}/{owner}/{repo_key}.git"


def check_upstream_repo(
    repo_key: str, cfg: Dict[str, Any]
) -> Tuple[bool, bool, List[str]]:
    """
    Return (check_success, repo_exists, branches).

    - check_success False -> leave inbox untouched
    - repo_exists False   -> delete inbox root
    - branches list is derived from upstream heads (filtered elsewhere)
    """

    url = build_remote_url(repo_key, cfg)
    log(f"Checking upstream for {repo_key} via {url}")
    success, heads, stderr = run_git_ls_remote(url, heads_only=True)

    if not success:
        if repo_missing_from_stderr(stderr):
            log(f"Upstream reports repo {repo_key!r} is missing.")
            return True, False, []
        log(f"Upstream check failed for {repo_key!r}; leaving inbox untouched.")
        return False, False, []

    if heads:
        log(f"Upstream branches for {repo_key}: {heads}")
        return True, True, heads

    # No heads found; check for any refs to distinguish between "no branches"
    # and "repo missing" diagnostics that still surfaced with exit code 0.
    all_success, refs, stderr_all = run_git_ls_remote(url, heads_only=False)
    if repo_missing_from_stderr(stderr_all):
        log(f"Upstream reports repo {repo_key!r} is missing.")
        return True, False, []

    if not all_success:
        log(f"Upstream ref probe failed for {repo_key!r}; leaving inbox untouched.")
        return False, False, []

    log(f"Upstream repo {repo_key!r} exists but has no branches.")
    return True, True, []


def sync_inbox_branches(repo_key: str, wanted: Iterable[str], reason: str) -> None:
    repo_root = INBOX_ROOT / repo_key
    repo_root.mkdir(parents=True, exist_ok=True)

    wanted_set: Set[str] = set(wanted)
    for br in sorted(wanted_set):
        ensure_inbox_dir(repo_key, br)

    existing = {child.name for child in repo_root.iterdir() if child.is_dir()}
    for stale in sorted(existing - wanted_set):
        remove_inbox_dir(repo_root / stale, reason=reason)


def reconcile_local_repo(repo_path: Path, tb_cfg: Dict[str, Any]) -> None:
    repo_key = repo_path.name
    log(f"Processing local repo {repo_key}")

    branches = list_remote_branches(repo_path)
    if not branches:
        log(f"  No remote branches found for {repo_key}.")

    wanted = filter_branches_for_inbox(
        branches,
        branch_mode=str(tb_cfg.get("branch_mode", "all")).lower(),
        whitelist=list(tb_cfg.get("branch_whitelist", [])),
        blacklist=list(tb_cfg.get("branch_blacklist", [])),
        name_blacklist=list(tb_cfg.get("branch_name_blacklist", ["HEAD"])),
    )
    log(f"  Wanted branches after filtering: {wanted}")

    sync_inbox_branches(
        repo_key,
        wanted,
        reason=(
            f"branch name is not present on origin for repo {repo_key!r} or was "
            f"filtered out by tree_builder settings"
        ),
    )
    mark_inbox_root_valid(INBOX_ROOT / repo_key)


def reconcile_upstream_repo(
    repo_key: str, cfg: Dict[str, Any], tb_cfg: Dict[str, Any]
) -> None:
    check_success, repo_exists, branches = check_upstream_repo(repo_key, cfg)
    if not check_success:
        return

    if not repo_exists:
        log(
            f"Upstream reports repo {repo_key!r} missing; keeping inbox root untouched."
        )
        return

    wanted = filter_branches_for_inbox(
        branches,
        branch_mode=str(tb_cfg.get("branch_mode", "all")).lower(),
        whitelist=list(tb_cfg.get("branch_whitelist", [])),
        blacklist=list(tb_cfg.get("branch_blacklist", [])),
        name_blacklist=list(tb_cfg.get("branch_name_blacklist", ["HEAD"])),
    )
    log(f"Upstream wanted branches for {repo_key}: {wanted}")

    sync_inbox_branches(
        repo_key,
        wanted,
        reason=(
            f"branch name is not present upstream for repo {repo_key!r} or was "
            f"filtered out by tree_builder settings"
        ),
    )
    mark_inbox_root_valid(INBOX_ROOT / repo_key)


def main() -> None:
    cfg, upstream_enabled = load_config()
    tb_cfg = cfg.get("tree_builder", {})
    watcher_cfg = cfg.get("watcher", {})
    eager_repos = bool(tb_cfg.get("eager_repos", False))
    greedy_inboxes = bool(tb_cfg.get("greedy_inboxes", False))

    global INBOX_ROOT, REPOS_ROOT
    INBOX_ROOT = Path(cfg.get("inbox", INBOX_ROOT))
    REPOS_ROOT = Path(cfg.get("repos_root", REPOS_ROOT))

    log(f"Loaded tree_builder config: {tb_cfg}")
    log(f"Ensuring inbox root exists at {INBOX_ROOT}")
    INBOX_ROOT.mkdir(parents=True, exist_ok=True)

    local_repos = {repo.name: repo for repo in discover_repos(REPOS_ROOT)}
    repo_keys: Set[str] = set(local_repos)
    if INBOX_ROOT.is_dir():
        repo_keys.update(child.name for child in INBOX_ROOT.iterdir() if child.is_dir())
    existing_repo_keys = set(repo_keys)

    upstream_only_repos: Set[str] = set()
    if greedy_inboxes and upstream_enabled:
        owner = watcher_cfg.get("git_default_owner") or ""
        host = watcher_cfg.get("git_default_host", "github.com")
        protocol = watcher_cfg.get("git_protocol", "https")
        token_env = watcher_cfg.get("git_api_token_env")

        if owner:
            log(f"greedy_inboxes=True: discovering repos for owner '{owner}' at {host}")
            discovered = discover_upstream_repos_for_owner(
                owner, host, protocol, token_env
            )
            upstream_repos = set(discovered)
            log(
                f"greedy_inboxes=True: discovered upstream repos: "
                f"{sorted(upstream_repos)}"
            )
            repo_keys |= upstream_repos
            upstream_only_repos = repo_keys - existing_repo_keys
            log(
                f"greedy_inboxes=True: repo set expanded from "
                f"{len(existing_repo_keys)} to {len(repo_keys)} (including upstream-only repos)"
            )
        else:
            log(
                "greedy_inboxes=True but git_default_owner is not configured; "
                "skipping upstream discovery."
            )

    if eager_repos:
        for repo_key in sorted(local_repos):
            repo_root = INBOX_ROOT / repo_key
            repo_root.mkdir(parents=True, exist_ok=True)
            log(f"Eager mode: ensured inbox repo root {repo_root}")

    for repo_key in sorted(repo_keys):
        repo_root = INBOX_ROOT / repo_key
        if not is_valid_repo_key(repo_key):
            mark_inbox_root_invalid(
                repo_root, reason="Repo key contains illegal characters."
            )
            continue
        if repo_key in local_repos:
            reconcile_local_repo(local_repos[repo_key], tb_cfg)
        else:
            if repo_key in upstream_only_repos:
                log(
                    f"Processing upstream-only repo {repo_key} (no local clone, "
                    "added via greedy_inboxes)."
                )
            else:
                log(
                    f"Processing inbox-only repo {repo_key} (no local clone "
                    "prior to greedy_inboxes)."
                )
            if upstream_enabled:
                reconcile_upstream_repo(repo_key, cfg, tb_cfg)
            else:
                log(
                    f"Inbox root '{repo_key}' has no local clone and upstream is disabled; "
                    "leaving it untouched."
                )

    log("Inbox tree rebuild complete.")


if __name__ == "__main__":
    main()
