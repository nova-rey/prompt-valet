#!/usr/bin/env python3
"""
codex_watcher.py

Watches an inbox tree for *.prompt.md files and runs them through the Codex CLI,
creating branches and PRs for each job.

This version:
- Uses YAML config at /srv/prompt-valet/config/prompt-valet.yaml (if present),
  merged on top of DEFAULT_CONFIG.
- Handles existing job branches by reusing + hard-resetting them instead of
  failing the whole job (fixes "branch already exists" errors on reruns).
- Treats prompt files as first-class inputs to Codex (we pass the file path
  directly rather than inlining its text).
"""

import argparse
import copy
import datetime as dt
import logging
import os
import queue
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
import textwrap
from typing import Any, Dict, Literal, Optional, Sequence, Tuple

import yaml  # type: ignore

from scripts import queue_runtime


# ---------------------------------------------------------------------------
# Config & constants
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_PATH = Path("/srv/prompt-valet/config/prompt-valet.yaml")
DEFAULT_PV_ROOT = Path("/srv/prompt-valet")
DEBOUNCE_SECONDS = 2
POLL_INTERVAL_SECONDS = 1.0

DEFAULT_CONFIG: Dict[str, Any] = {
    "inbox": str(DEFAULT_PV_ROOT / "inbox"),
    "processed": str(DEFAULT_PV_ROOT / "processed"),
    "finished": str(DEFAULT_PV_ROOT / "finished"),
    "repos_root": str(DEFAULT_PV_ROOT / "repos"),
    "pv_root": str(DEFAULT_PV_ROOT),
    "failed": str(DEFAULT_PV_ROOT / "failed"),
    "queue": {
        "enabled": False,
        "max_retries": 3,
        "failure_archive": False,
    },
    "watcher": {
        "auto_clone_missing_repos": True,
        "git_default_owner": "nova-rey",
        "git_default_host": "github.com",
        "git_protocol": "https",
        "runner_cmd": "codex",
        "runner_model": "gpt-5.1-codex-mini",
        "runner_sandbox": "danger-full-access",
    },
}

# Simple global-ish config; populated in main()
CONFIG: Dict[str, Any] = {}
INBOX_MODE = "legacy_single_owner"
JOB_STATES: Dict[str, str] = {}

STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------


def now_utc_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def log(msg: str) -> None:
    ts = now_utc_iso()
    print(f"[codex_watcher] [{ts}] {msg}", flush=True)


def _emit_job_event(
    event: str,
    *,
    job_record: queue_runtime.JobRecord,
    reason: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    repo = f"{job_record.git_owner}/{job_record.repo_name}"
    parts = [
        f"event={event}",
        f"job_id={job_record.job_id}",
        f"state={job_record.state}",
        f"repo={repo}",
        f"branch={job_record.branch_name}",
    ]
    if reason:
        parts.append(f"reason={reason}")
    if extra:
        for key, value in extra.items():
            parts.append(f"{key}={value}")
    log("[prompt-valet] " + " ".join(parts))


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def run_cmd(cmd: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    """Run a shell command, returning (returncode, stdout, stderr)."""

    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd is not None else None,
        check=False,
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _run_git(
    args: Sequence[str],
    *,
    cwd: Path,
    logger: logging.Logger,
    check: bool = False,
) -> subprocess.CompletedProcess:
    """Run a git command in `cwd` and capture output.

    If `check` is True, non–zero returncodes are logged and raised.
    Otherwise, the caller must examine `returncode`.
    """

    cmd = ["git", *args]
    logger.debug("Running git command: %s (cwd=%s)", " ".join(cmd), cwd)
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        logger.warning(
            "git command failed (rc=%s): %s\nstdout:\n%s\nstderr:\n%s",
            proc.returncode,
            " ".join(cmd),
            proc.stdout,
            proc.stderr,
        )
        if check:
            proc.check_returncode()
    return proc


def run_git(
    args, cwd: Path, allow_failure: bool = False
) -> subprocess.CompletedProcess:
    """
    Run a git command, logging stdout/stderr.

    If allow_failure is False, raise RuntimeError on non-zero return code.
    """
    normalized_args = list(args)
    if normalized_args and normalized_args[0] == "git":
        normalized_args = normalized_args[1:]

    cmd = ["git"] + normalized_args
    log(f"RUN: {cmd!r} (cwd={cwd})")
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        capture_output=True,
    )

    if proc.stdout:
        log(f"STDOUT:\n{proc.stdout.rstrip()}")
    if proc.stderr:
        log(f"STDERR:\n{proc.stderr.rstrip()}")

    if proc.returncode != 0 and not allow_failure:
        err_msg = f"Command failed with code {proc.returncode}: {cmd!r}"
        if proc.stderr:
            err_msg += f" stderr: {proc.stderr.rstrip()}"
        raise RuntimeError(err_msg)
    return proc


def get_remote_branch_names(repo_dir: Path, logger: logging.Logger) -> set[str]:
    """
    Return the set of remote branch names known on origin for this repo.
    """
    proc = run_git(["ls-remote", "--heads", "origin"], cwd=repo_dir)
    branches: set[str] = set()
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2:
            continue
        _, ref = parts
        prefix = "refs/heads/"
        if ref.startswith(prefix):
            branches.add(ref[len(prefix) :])
    logger.debug("Remote branches for %s: %s", repo_dir, ", ".join(sorted(branches)))
    return branches


def ensure_worker_repo_clean_and_synced(
    repo_path: Path, base_branch: str, logger: logging.Logger
) -> bool:
    """Ensure the worker repo is usable before running Codex.

    The worker repo is treated as disposable; if it is dirty, we reset/clean it
    and continue rather than failing the job.
    """

    git_dir = repo_path / ".git"
    repo_root = repo_path.parent.parent
    git_owner = repo_path.parent.name
    repo_name = repo_path.name

    def _fresh_clone() -> bool:
        if repo_path.exists():
            logger.info(
                "Git preflight: replacing worker repo at %s to ensure clean state.",
                repo_path,
            )
            shutil.rmtree(repo_path)
        try:
            ensure_repo_cloned(repo_root, git_owner, repo_name)
        except Exception:
            logger.exception(
                "Git preflight: failed to clone worker repo at %s; skipping Codex run.",
                repo_path,
            )
            return False
        return True

    def _checkout_base_and_pull() -> bool:
        for args in (["checkout", base_branch], ["pull", "--ff-only"]):
            proc = _run_git(args, cwd=repo_path, logger=logger)
            if proc.returncode != 0:
                logger.error(
                    "Git preflight: command failed (rc=%s): git %s\nstderr:\n%s",
                    proc.returncode,
                    " ".join(args),
                    proc.stderr.strip(),
                )
                return False
        return True

    if not git_dir.is_dir():
        logger.info("Git preflight: repo missing or invalid; performing fresh clone.")
        if not _fresh_clone():
            return False
    else:
        status = _run_git(["status", "--porcelain"], cwd=repo_path, logger=logger)
        if status.returncode != 0:
            logger.error(
                "Git preflight: `git status` failed in %s; skipping Codex run.",
                repo_path,
            )
            return False
        if status.stdout.strip():
            logger.info(
                "Git preflight: repo dirty; removing and recloning to discard local changes."
            )
            if not _fresh_clone():
                return False

    if not git_dir.is_dir():
        # Fresh clone failed.
        return False

    if not _checkout_base_and_pull():
        return False

    logger.info(
        "Git preflight: repo clean, on %s, and synced; proceeding with Codex run.",
        base_branch,
    )
    return True


def resolve_prompt_repo(
    config: dict, prompt_path: str
) -> Tuple[str, str, str, Path, Path]:
    """
    Resolve a prompt path under the inbox root into:

        (owner, repo_name, branch_name, repo_root, rel_path)

    Behavior depends on config["inbox_mode"]:

    - legacy_single_owner:
        inbox layout: <repo>/<branch>/.../<file>
        owner is taken from config["git_owner"].

    - multi_owner:
        inbox layout: <owner>/<repo>/<branch>/.../<file>
        owner is the first segment.

    The repo_root is always:

        repos_root/<owner>/<repo_name>
    """
    inbox_root = Path(config["inbox"]).expanduser().resolve()
    repos_root = Path(config["repos_root"]).expanduser().resolve()

    mode = config.get("inbox_mode", "legacy_single_owner")

    prompt = Path(prompt_path).expanduser().resolve()
    try:
        rel = prompt.relative_to(inbox_root)
    except ValueError as exc:
        raise RuntimeError(
            f"Prompt path {prompt} is not under inbox root {inbox_root}"
        ) from exc
    parts = rel.parts

    if mode == "legacy_single_owner":
        # Expect at least <repo>/<branch>/.../<file>
        if len(parts) < 2:
            raise RuntimeError(
                f"Cannot derive repo from prompt path {prompt} in legacy_single_owner "
                f"mode: expected <repo>/<branch>/..., got {rel}"
            )

        git_owner = config.get("git_owner")
        if not git_owner:
            raise RuntimeError(
                "Configuration missing required 'git_owner' key in "
                "legacy_single_owner mode."
            )

        owner = git_owner
        repo_name = parts[0]
        branch_name = parts[1]

    elif mode == "multi_owner":
        # Expect at least <owner>/<repo>/<branch>/.../<file>
        if len(parts) < 3:
            raise RuntimeError(
                f"Cannot derive repo from prompt path {prompt} in multi_owner mode: "
                f"expected <owner>/<repo>/<branch>/..., got {rel}"
            )

        owner = parts[0]
        repo_name = parts[1]
        branch_name = parts[2]

    else:
        raise RuntimeError(
            f"Unknown inbox_mode '{mode}' in configuration; expected "
            "'legacy_single_owner' or 'multi_owner'."
        )

    repo_root = repos_root / owner / repo_name
    return owner, repo_name, branch_name, repo_root, rel


def derive_repo_root_from_prompt(config: dict, prompt_path: str) -> Path:
    """
    Backwards-compatible helper used by tests and callers that only care about
    the repo root path. Delegates to resolve_prompt_repo().
    """
    _, _, _, repo_root, _ = resolve_prompt_repo(config, prompt_path)
    return repo_root


def ensure_repo_cloned(repo_root: Path, git_owner: str, repo_name: str) -> Path:
    """
    Ensure <repo_root>/<git_owner>/<repo_name> exists and is a git clone.

    If missing and auto_clone_missing_repos is True, clone from origin built
    from config (owner/host/protocol).
    """
    target = repo_root / git_owner / repo_name
    if target.is_dir() and (target / ".git").is_dir():
        return target

    watcher_cfg = CONFIG.get("watcher", {})
    auto_clone = bool(watcher_cfg.get("auto_clone_missing_repos", True))
    if not auto_clone:
        raise RuntimeError(
            f"Repo {git_owner!r}/{repo_name!r} missing and auto_clone_disabled"
        )

    owner = git_owner or watcher_cfg.get("git_default_owner", "nova-rey")
    host = watcher_cfg.get("git_default_host", "github.com")
    proto = watcher_cfg.get("git_protocol", "https")

    url = f"{proto}://{host}/{owner}/{repo_name}.git"
    log(f"Cloning missing repo {repo_name!r} from {url!r} into {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    run_git(["clone", url, str(target)], cwd=repo_root)
    return target


def ensure_agent_branch(repo_dir: Path, job_branch: str) -> None:
    """Create the agent branch if missing, otherwise switch to it."""
    try:
        run_git(["checkout", "-b", job_branch], cwd=repo_dir)
    except RuntimeError as exc:
        msg = str(exc)
        stderr = getattr(exc, "stderr", "")
        if "already exists" in msg or "already exists" in stderr:
            run_git(["checkout", job_branch], cwd=repo_dir)
        else:
            raise


def prepare_branch(
    repo_dir: Path,
    job_branch: str,
    base_branch: str,
) -> None:
    """
    Make sure the repo is on a clean job branch derived from base_branch.

    - Fetches origin.
    - Checks out and fast-forwards base_branch.
    - Creates job_branch from base_branch if missing, otherwise reuses it.
    """
    # Ensure repo exists & has remotes (non-fatal if this fails)
    run_git(["remote", "-v"], cwd=repo_dir, allow_failure=True)

    # Fetch latest and get onto base branch
    run_git(["fetch", "origin", base_branch], cwd=repo_dir)
    run_git(["checkout", base_branch], cwd=repo_dir)
    run_git(["reset", "--hard", f"origin/{base_branch}"], cwd=repo_dir)
    run_git(["clean", "-fd"], cwd=repo_dir)

    if job_branch == base_branch:
        return

    ensure_agent_branch(repo_dir, job_branch)


# ---------------------------------------------------------------------------
# Pre-execution Git sync (Solution C)
# ---------------------------------------------------------------------------


def run_git_sync(repo_path: str) -> None:
    repo = Path(repo_path).expanduser().resolve()
    git_dir = repo / ".git"

    if not git_dir.is_dir():
        print(
            f"[codex_watcher] ERROR: repo path is not a Git repository: {repo} "
            "(.git directory not found)."
        )
        raise RuntimeError(
            "Git synchronization failed: target directory is not a Git repository."
        )

    try:
        subprocess.run(
            ["git", "fetch", "origin"],
            cwd=str(repo),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        subprocess.run(
            ["git", "reset", "--hard", "origin/main"],
            cwd=str(repo),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        print(
            f"[codex_watcher] Repository synchronized at {repo} "
            "(fetch + reset --hard origin/main)."
        )
    except subprocess.CalledProcessError as e:
        stderr = (
            e.stderr.decode(errors="ignore") if getattr(e, "stderr", None) else str(e)
        )
        print("[codex_watcher] ERROR: Git synchronization failed.")
        print(stderr)
        raise RuntimeError(
            "Git synchronization failed; aborting prompt execution."
        ) from e


# ---------------------------------------------------------------------------
# Inbox lifecycle helpers
# ---------------------------------------------------------------------------


def _statusified_name(name: str, status: str) -> str:
    """
    Given an original filename like 'xyz.prompt.md', return a new filename
    with the status inserted before the final extension:

        xyz.prompt.md + running -> xyz.running.md
    """
    # We deliberately replace only the first '.prompt' occurrence to avoid
    # weird multi-dot filenames, but keep the overall pattern simple:
    if name.endswith(".prompt.md"):
        base = name[: -len(".prompt.md")]
        return f"{base}.{status}.md"
    # Fallback: just insert before the last dot
    stem, dot, ext = name.rpartition(".")
    if not dot:
        return f"{name}.{status}"
    return f"{stem}.{status}.{ext}"


def _job_key(rel: Path) -> str:
    """Normalize a job key for de-duplication across watcher loops."""
    return str(rel)


def _prompt_rel_from_running(rel_running: Path) -> Path:
    """Return the original *.prompt.md relative path for a running file."""
    if rel_running.name.endswith(".running.md"):
        prompt_name = rel_running.name[: -len(".running.md")] + ".prompt.md"
        return rel_running.with_name(prompt_name)
    return rel_running


def claim_inbox_prompt(inbox_root: Path, rel: Path) -> Path:
    """
    Atomically claim a prompt in the inbox by renaming:

        xyz.prompt.md -> xyz.running.md

    Returns the full path to the new .running file.

    Raises FileNotFoundError if the original .prompt.md is missing.
    """
    src = inbox_root / rel
    if not src.exists():
        raise FileNotFoundError(src)

    running_name = _statusified_name(src.name, STATUS_RUNNING)
    dst = src.with_name(running_name)
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.replace(dst)
    return dst


def finalize_inbox_prompt(
    inbox_root: Path,
    finished_root: Path,
    rel: Path,
    status: Literal["done", "error"],
    delay_seconds: float = 5.0,
) -> None:
    """
    Rename the claimed .running file in the inbox to .done or .error,
    leave it there briefly, then move it into the finished tree.

    'rel' should be the original relative path under inbox, e.g.
    prompt-valet/main/xyz.prompt.md; this function derives the correct
    running/done/error names from that.
    """
    original = inbox_root / rel

    # Derive the running and final names from the original filename.
    running_name = _statusified_name(original.name, STATUS_RUNNING)
    running_path = original.with_name(running_name)

    final_name = _statusified_name(original.name, status)
    final_inbox_path = original.with_name(final_name)

    if running_path.exists():
        running_path.replace(final_inbox_path)
    elif final_inbox_path.exists():
        # Idempotency / partial runs: if it's already renamed, just continue.
        pass
    else:
        # File is missing entirely; nothing to move. Log and exit quietly.
        print(
            f"[prompt-valet] Warning: expected inbox file for {rel} in status "
            f"{STATUS_RUNNING}, but none found; skipping finalize."
        )
        return

    # Short grace period so operators can see the .done/.error in inbox.
    if delay_seconds > 0:
        time.sleep(delay_seconds)

    # Move to finished tree, preserving the relative path structure.
    finished_rel = rel.with_name(final_name)
    finished_path = finished_root / finished_rel
    finished_path.parent.mkdir(parents=True, exist_ok=True)
    final_inbox_path.replace(finished_path)


def start_jobs_from_running(
    inbox_root: Path,
    processed_root: Path,
    job_queue: Optional["queue.Queue[Job]"],
    *,
    queue_enabled: bool = False,
    queue_root: Optional[Path] = None,
) -> None:
    """
    Phase B of the watcher loop: start jobs based on *.running.md files.

    All job metadata is derived from the running file; the job is only marked
    RUNNING after the file has been copied into the run directory.
    """

    for running_path in inbox_root.rglob("*.running.md"):
        if not running_path.is_file():
            continue

        try:
            rel_running = running_path.relative_to(inbox_root)
        except ValueError:
            continue

        prompt_rel = _prompt_rel_from_running(rel_running)
        key = _job_key(prompt_rel)
        if JOB_STATES.get(key) in {STATUS_RUNNING, STATUS_DONE, STATUS_ERROR}:
            continue

        try:
            git_owner, repo_name, branch_name, _, _ = resolve_prompt_repo(
                CONFIG, str(inbox_root / prompt_rel)
            )
        except RuntimeError as exc:
            print(
                f"[codex_watcher] Skipping running prompt {running_path}: "
                f"unable to derive repo root ({exc})."
            )
            continue

        if queue_enabled:
            if queue_root is None:
                raise RuntimeError("Queue root is required when queue is enabled")
            _enqueue_queue_job(
                running_path=running_path,
                prompt_rel=prompt_rel,
                git_owner=git_owner,
                repo_name=repo_name,
                branch_name=branch_name,
                queue_root=queue_root,
            )
            continue

        assert job_queue is not None
        run_id = dt.datetime.utcnow().strftime("%Y%m%d-%H%M-%S")
        run_root = processed_root / git_owner / repo_name / branch_name / run_id
        run_root.mkdir(parents=True, exist_ok=True)

        prompt_copy_path = run_root / "prompt.md"
        try:
            shutil.copy2(running_path, prompt_copy_path)
        except FileNotFoundError:
            log(
                "[prompt-valet] Warning: running prompt missing during job "
                "startup; deferring until next loop."
            )
            continue

        job = Job(
            git_owner=git_owner,
            repo_name=repo_name,
            branch_name=branch_name,
            job_id=run_id,
            inbox_rel=prompt_rel,
            inbox_path=running_path,
            run_root=run_root,
            prompt_path=prompt_copy_path,
        )

        JOB_STATES[key] = STATUS_RUNNING
        log("[prompt-valet] queued job " f"prompt={prompt_rel} run_root={run_root}")
        job_queue.put(job)


def _enqueue_queue_job(
    running_path: Path,
    prompt_rel: Path,
    git_owner: str,
    repo_name: str,
    branch_name: str,
    queue_root: Path,
) -> None:
    key = _job_key(prompt_rel)
    inbox_file = str(running_path)
    existing = queue_runtime.find_job_for_inbox(queue_root, inbox_file)
    if existing:
        return

    job_record = queue_runtime.enqueue_job(
        queue_root,
        git_owner=git_owner,
        repo_name=repo_name,
        branch_name=branch_name,
        inbox_file=inbox_file,
        inbox_rel=str(prompt_rel),
        reason="new_prompt",
    )

    JOB_STATES[key] = STATUS_RUNNING
    log("[prompt-valet] enqueued job " f"prompt={prompt_rel} queue={job_record.job_id}")
    _emit_job_event(
        "job.created",
        job_record=job_record,
        reason="new_prompt",
        extra={"queue_path": str(job_record.job_dir)},
    )


def claim_new_prompts(inbox_root: Path) -> None:
    """
    Phase A of the watcher loop: claim *.prompt.md files after a short debounce.

    The debounce protects against race conditions where the file is still being
    written; real job creation is deferred to processing of *.running.md files.
    """

    now = time.time()
    for path in inbox_root.rglob("*.prompt.md"):
        if not path.is_file():
            continue

        running_candidate = path.with_name(_statusified_name(path.name, STATUS_RUNNING))
        if running_candidate.exists():
            continue

        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            continue

        if now - mtime < DEBOUNCE_SECONDS:
            continue

        rel = path.relative_to(inbox_root)
        try:
            running_path = claim_inbox_prompt(inbox_root, rel)
        except FileNotFoundError:
            continue
        else:
            log(f"Claimed prompt {rel} as {running_path.name}")


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def normalize_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    watcher_cfg = cfg.get("watcher", {})

    # Normalize top-level keys from watcher section for backwards compatibility.
    if "git_owner" not in cfg and "git_default_owner" in watcher_cfg:
        cfg["git_owner"] = watcher_cfg["git_default_owner"]

    if "git_host" not in cfg and "git_default_host" in watcher_cfg:
        cfg["git_host"] = watcher_cfg["git_default_host"]

    if "inbox_mode" not in cfg and "inbox_mode" in watcher_cfg:
        cfg["inbox_mode"] = watcher_cfg["inbox_mode"]

    queue_cfg = cfg.setdefault("queue", {})
    queue_cfg.setdefault("enabled", False)
    queue_cfg.setdefault("max_retries", 3)
    queue_cfg.setdefault("failure_archive", False)
    queue_cfg.setdefault("jobs_root", None)

    return cfg


def _queue_root_from_config(cfg: Dict[str, Any]) -> Path:
    queue_cfg = cfg.get("queue", {})
    override = queue_cfg.get("jobs_root")
    if override:
        return Path(override).expanduser().resolve()
    pv_root = Path(cfg["pv_root"]).expanduser().resolve()
    return pv_root / ".queue" / "jobs"


def load_config() -> tuple[Dict[str, Any], Path]:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    path = DEFAULT_CONFIG_PATH

    loaded_path: str = "<defaults>"
    user_cfg: Dict[str, Any] = {}

    if path.is_file():
        try:
            user_cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(user_cfg, dict):
                raise ValueError("YAML config is not a mapping at the top level")
            for key, value in user_cfg.items():
                if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                    cfg[key].update(value)  # type: ignore
                else:
                    cfg[key] = value
            loaded_path = str(path)
        except Exception as exc:  # pragma: no cover
            log(
                f"Failed to load YAML config at {path}: {exc}; "
                "falling back to defaults"
            )
            user_cfg = {}
            loaded_path = "<defaults>"

    cfg = normalize_config(cfg)

    watcher_cfg = cfg.get("watcher", {})
    queue_cfg = cfg.get("queue", {})

    pv_root = Path(cfg.get("pv_root", str(DEFAULT_PV_ROOT))).expanduser().resolve()

    dir_defaults = {
        "inbox": "inbox",
        "processed": "processed",
        "finished": "finished",
        "repos_root": "repos",
        "failed": "failed",
    }

    explicit_dirs = {key for key in dir_defaults if key in user_cfg}

    resolved_dirs: Dict[str, Path] = {}
    for key, subdir in dir_defaults.items():
        raw_value = cfg.get(key) if key in explicit_dirs else str(pv_root / subdir)
        resolved_path = Path(raw_value).expanduser().resolve()
        resolved_dirs[key] = resolved_path
        cfg[key] = str(resolved_path)

    inbox_root = resolved_dirs["inbox"]
    processed_root = resolved_dirs["processed"]
    finished_root = resolved_dirs["finished"]
    repos_root = resolved_dirs["repos_root"]
    failed_root = resolved_dirs["failed"]

    pv_root.mkdir(parents=True, exist_ok=True)
    inbox_root.mkdir(parents=True, exist_ok=True)
    processed_root.mkdir(parents=True, exist_ok=True)
    finished_root.mkdir(parents=True, exist_ok=True)
    repos_root.mkdir(parents=True, exist_ok=True)
    failed_root.mkdir(parents=True, exist_ok=True)

    cfg["pv_root"] = str(pv_root)
    cfg["queue"] = queue_cfg

    runner_cmd = watcher_cfg.get("runner_cmd", "codex")
    log(
        "[prompt-valet] loaded config="
        f"{loaded_path} "
        f"inbox={inbox_root} "
        f"processed={processed_root} "
        f"pv_root={pv_root} "
        f"git_owner={cfg.get('git_owner')} "
        f"git_host={cfg.get('git_host')} "
        f"git_protocol={watcher_cfg.get('git_protocol', 'https')} "
        f"runner={runner_cmd} exec "
        f"queue.enabled={queue_cfg.get('enabled')}"
    )

    config_label = str(path) if path.is_file() else "<defaults>"
    log(f"Using pv_root={pv_root} config={config_label}")

    return cfg, pv_root


def load_config_from_dict(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize an existing config dict (primarily for tests)."""
    normalized = copy.deepcopy(DEFAULT_CONFIG)
    for key, value in cfg.items():
        if isinstance(value, dict) and isinstance(normalized.get(key), dict):
            normalized[key].update(value)  # type: ignore[arg-type]
        else:
            normalized[key] = value
    return normalize_config(normalized)


# ---------------------------------------------------------------------------
# Job model
# ---------------------------------------------------------------------------


@dataclass
class Job:
    git_owner: str
    repo_name: str
    branch_name: str
    job_id: str
    inbox_rel: Path
    inbox_path: Path
    run_root: Path
    prompt_path: Path


def get_job_base_branch(job: Job) -> str:
    """
    Return the upstream base branch this job should target.

    Uses the inbox branch folder from job.branch_name.
    Does NOT silently fall back to 'main'; callers must handle missing branches.
    """
    if not job.branch_name:
        raise ValueError(f"Job {job.job_id} is missing branch_name")
    return job.branch_name


class MissingBaseBranchError(RuntimeError):
    """Raised when the desired base branch is absent on the upstream remote."""


def run_codex_for_job(repo_dir: Path, job: Job, run_root: Path) -> None:
    """
    Invoke the Codex CLI using the prompt file as the input prompt.

    We use --output-last-message so we can archive whatever Codex prints as
    the final message of the run into docs/AGENT_RUNS.
    """
    watcher_cfg = CONFIG.get("watcher", {})
    cmd = watcher_cfg.get("runner_cmd", "codex")
    model = watcher_cfg.get("runner_model", "gpt-5.1-codex-mini")
    sandbox = watcher_cfg.get("runner_sandbox", "danger-full-access")

    runs_dir = repo_dir / "docs" / "AGENT_RUNS"
    runs_dir.mkdir(parents=True, exist_ok=True)

    stamp = dt.datetime.utcnow().strftime("%Y%m%d-%H%M-%S")
    out_file = runs_dir / f"codex-run-{stamp}.md"

    env = os.environ.copy()
    prompt_path = Path(job.prompt_path)

    env.update(
        {
            "PV_RUN_ID": job.job_id,
            "PV_RUN_ROOT": str(run_root),
            "PV_PROMPT_FILE": str(prompt_path),
        }
    )

    cli_cmd = [
        cmd,
        "exec",
        "--skip-git-repo-check",
        "--cd",
        str(repo_dir),
        "--output-last-message",
        str(out_file),
        "--model",
        model,
        "--sandbox",
        sandbox,
        str(prompt_path),
    ]

    log(f"Running Codex CLI for job {job!r}")
    proc = subprocess.run(
        cli_cmd,
        text=True,
        capture_output=True,
        env=env,
    )
    if proc.stdout:
        log(f"codex STDOUT:\n{proc.stdout.rstrip()}")
    if proc.stderr:
        log(f"codex STDERR:\n{proc.stderr.rstrip()}")

    if proc.returncode != 0:
        raise RuntimeError(f"Codex CLI failed with code {proc.returncode}: {cli_cmd!r}")


def create_pr_for_job(job: Job, repo_dir: Path, logger: logging.Logger) -> None:
    """
    From a clean repo with Codex changes applied, create a branch, commit, push,
    and open a GitHub PR.

    This function must never raise; on failure it logs and returns so the
    watcher can continue processing future jobs.
    """

    rc, out, err = run_cmd(["git", "status", "--porcelain"], cwd=repo_dir)
    if rc != 0:
        logger.error("PR: git status failed (rc=%s): %s\n%s", rc, out, err)
        return

    if not out.strip():
        logger.info("PR: no changes detected, skipping PR creation.")
        return

    base_branch = get_job_base_branch(job)
    remote_branches = get_remote_branch_names(repo_dir, logger)
    if base_branch not in remote_branches:
        reason = (
            f"Base branch '{base_branch}' not found on origin for repo "
            f"'{job.git_owner}/{job.repo_name}'; refusing to fall back to 'main'."
        )
        logger.error(reason)
        raise MissingBaseBranchError(reason)

    logger.info(
        "Opening PR for job %s against base branch %s",
        job.job_id,
        base_branch,
    )

    prompt_slug = (
        job.inbox_rel.stem.replace(" ", "-").replace("_", "-").replace(".", "-")
    )
    timestamp = job.job_id
    branch_name = f"codex/{prompt_slug}-{timestamp}"

    logger.info("PR: preparing branch %s", branch_name)

    rc, out, err = run_cmd(["git", "checkout", base_branch], cwd=repo_dir)
    if rc != 0:
        logger.error(
            "PR: git checkout %s failed (rc=%s): %s\n%s",
            base_branch,
            rc,
            out,
            err,
        )
        return

    rc, out, err = run_cmd(["git", "pull", "--ff-only"], cwd=repo_dir)
    if rc != 0:
        logger.error("PR: git pull failed (rc=%s): %s\n%s", rc, out, err)
        return

    rc, out, err = run_cmd(["git", "checkout", "-b", branch_name], cwd=repo_dir)
    if rc != 0:
        logger.error(
            "PR: git checkout -b %s failed (rc=%s): %s\n%s",
            branch_name,
            rc,
            out,
            err,
        )
        return

    rc, out, err = run_cmd(["git", "add", "-A"], cwd=repo_dir)
    if rc != 0:
        logger.error("PR: git add -A failed (rc=%s): %s\n%s", rc, out, err)
        return

    title = f"Codex: {job.inbox_rel.name}"
    body = textwrap.dedent(
        f"""
        Automated Codex run for prompt:

        - Prompt file: `{job.inbox_rel.name}`
        - Job ID: `{job.job_id}`

        Generated by the local Prompt Valet runner.
        """
    ).strip()

    commit_msg = f"{title} (job {job.job_id})"

    rc, out, err = run_cmd(["git", "commit", "-m", commit_msg], cwd=repo_dir)
    if rc != 0:
        if "nothing to commit" in out.lower() or "nothing to commit" in err.lower():
            logger.info("PR: nothing to commit after git add; skipping PR.")
        else:
            logger.error("PR: git commit failed (rc=%s): %s\n%s", rc, out, err)
        return

    rc, out, err = run_cmd(["git", "push", "-u", "origin", branch_name], cwd=repo_dir)
    if rc != 0:
        logger.error("PR: git push failed (rc=%s): %s\n%s", rc, out, err)
        return

    rc, out, err = run_cmd(
        [
            "gh",
            "pr",
            "create",
            "--title",
            title,
            "--body",
            body,
            "--base",
            base_branch,
            "--head",
            branch_name,
        ],
        cwd=repo_dir,
    )
    if rc != 0:
        logger.error("PR: gh pr create failed (rc=%s): %s\n%s", rc, out, err)
        return

    logger.info("PR: successfully created PR for branch %s", branch_name)


def run_prompt_job(job: Job) -> bool:
    """
    Run a single job end-to-end:
    - make sure repo is cloned
    - prepare job branch
    - run Codex
    - create a PR if Codex changed anything
    """
    repos_root = Path(CONFIG["repos_root"]).expanduser().resolve()

    logger = logging.getLogger("codex_watcher")

    original_prompt_path = Path(CONFIG["inbox"]) / job.inbox_rel
    repo_dir = derive_repo_root_from_prompt(CONFIG, str(original_prompt_path))
    repo_dir = ensure_repo_cloned(repos_root, job.git_owner, job.repo_name)

    base_branch = get_job_base_branch(job)
    if not ensure_worker_repo_clean_and_synced(repo_dir, base_branch, logger):
        logger.error(
            "Git preflight: unrecoverable git error; skipping Codex run for %s.",
            job.prompt_path,
        )
        JOB_STATES.pop(_job_key(job.inbox_rel), None)
        return False

    job_branch = job.branch_name
    prepare_branch(repo_dir, job_branch, base_branch=base_branch)

    run_root = job.run_root
    run_root.mkdir(parents=True, exist_ok=True)
    prompt_path = Path(job.prompt_path)
    prompt_exists = prompt_path.exists()

    codex_success = False
    log(
        "[prompt-valet] "
        f"run={run_root.name} repo={job.git_owner}/{job.repo_name} "
        f"branch={job.branch_name} prompt_inbox={job.inbox_path} "
        f"prompt_copy={prompt_path} processed={run_root}"
    )

    if prompt_exists:
        try:
            run_codex_for_job(repo_dir, job, run_root)
            codex_success = True
        except Exception:
            logger.exception("Codex run failed for job %s; skipping PR.", job.job_id)
            raise
    else:
        log(
            "[prompt-valet] Warning: prompt copy missing in run directory; "
            "continuing with no-op Codex run."
        )
        no_input = run_root / "NO_INPUT.md"
        no_input.write_text(
            "This run started without a prompt file. Likely the prompt "
            "referenced inbox paths or moved itself. Execution continued "
            "safely.\n"
        )
        codex_success = True

    if codex_success:
        try:
            create_pr_for_job(job, repo_dir, logger)
        except MissingBaseBranchError:
            logger.exception("PR: missing base branch; marking job as failed.")
            raise
        except Exception:
            logger.exception(
                "Unhandled exception during PR creation; continuing anyway."
            )

    return True


def worker(job_queue: "queue.Queue[Job]", stop_event: threading.Event) -> None:
    """
    Worker loop: pull jobs from queue and process serially.
    """
    inbox_root = Path(CONFIG["inbox"])
    finished_root = Path(CONFIG["finished"])

    while not (stop_event.is_set() and job_queue.empty()):
        try:
            job = job_queue.get(timeout=1.0)
        except queue.Empty:
            continue

        try:
            log(f"Processing job {job!r}")
            success = run_prompt_job(job)
        except Exception as exc:
            log(f"Error processing {job.inbox_path}: {exc!r}")
            JOB_STATES[_job_key(job.inbox_rel)] = STATUS_ERROR
            finalize_inbox_prompt(
                inbox_root=inbox_root,
                finished_root=finished_root,
                rel=job.inbox_rel,
                status=STATUS_ERROR,
            )
        else:
            if not success:
                log(
                    "Git preflight: unrecoverable git error; leaving prompt %s in inbox for retry.",
                    job.inbox_rel,
                )
                continue
            JOB_STATES[_job_key(job.inbox_rel)] = STATUS_DONE
            finalize_inbox_prompt(
                inbox_root=inbox_root,
                finished_root=finished_root,
                rel=job.inbox_rel,
                status=STATUS_DONE,
            )
        finally:
            job_queue.task_done()


def _prepare_run_copy(
    job_record: queue_runtime.JobRecord, processed_root: Path
) -> tuple[Path, Path]:
    run_root = (
        processed_root
        / job_record.git_owner
        / job_record.repo_name
        / job_record.branch_name
        / job_record.job_id
    )
    run_root.mkdir(parents=True, exist_ok=True)
    prompt_copy = run_root / "prompt.md"
    shutil.copy2(job_record.inbox_file, prompt_copy)
    return run_root, prompt_copy


def _build_job_from_queue_record(
    job_record: queue_runtime.JobRecord, run_root: Path, prompt_copy: Path
) -> Job:
    return Job(
        git_owner=job_record.git_owner,
        repo_name=job_record.repo_name,
        branch_name=job_record.branch_name,
        job_id=job_record.job_id,
        inbox_rel=Path(job_record.inbox_rel),
        inbox_path=Path(job_record.inbox_file),
        run_root=run_root,
        prompt_path=prompt_copy,
    )


def _archive_prompt_file(job: Job, target_root: Path) -> Optional[Path]:
    dest_dir = (
        target_root / job.git_owner / job.repo_name / job.branch_name / job.job_id
    )
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / job.inbox_rel.name
    try:
        Path(job.inbox_path).replace(dest_path)
    except FileNotFoundError:
        log(
            f"[prompt-valet] Warning: unable to archive prompt {job.inbox_rel}; "
            "file missing."
        )
        return None
    return dest_path


def _handle_queue_failure(
    queue_job: queue_runtime.JobRecord,
    *,
    job: Optional[Job],
    failure_reason: str,
    retryable: bool,
    failure_archive: bool,
    failed_root: Path,
    max_retries: int,
) -> queue_runtime.JobRecord:
    can_retry = retryable and queue_runtime.should_retry(queue_job, max_retries)
    if can_retry:
        queue_job = queue_runtime.mark_failed(
            queue_job, retryable=True, reason=failure_reason
        )
        _emit_job_event(
            "job.failed.retryable",
            job_record=queue_job,
            extra={
                "failure_reason": failure_reason,
                "retries": queue_job.retries,
            },
        )
        queue_job = queue_runtime.requeue(queue_job)
        _emit_job_event(
            "job.requeued",
            job_record=queue_job,
            extra={"retries": queue_job.retries},
        )
        return queue_job

    archived_path = None
    if failure_archive and job:
        archived_path = _archive_prompt_file(job, failed_root)

    queue_job = queue_runtime.mark_failed(
        queue_job,
        retryable=False,
        reason=failure_reason,
        archived_path=str(archived_path) if archived_path else None,
    )
    if archived_path:
        _emit_job_event(
            "job.archived",
            job_record=queue_job,
            extra={"archived_path": str(archived_path)},
        )
    _emit_job_event(
        "job.failed.final",
        job_record=queue_job,
        extra={"failure_reason": failure_reason},
    )
    job_rel = job.inbox_rel if job else Path(queue_job.inbox_rel)
    JOB_STATES[_job_key(job_rel)] = STATUS_ERROR
    return queue_job


def _process_queue_job(
    job_record: queue_runtime.JobRecord,
    *,
    processed_root: Path,
    failed_root: Path,
    failure_archive: bool,
    max_retries: int,
) -> None:
    queue_job = queue_runtime.mark_running(job_record, reason="executor")
    started_at = dt.datetime.utcnow()
    _emit_job_event(
        "job.running",
        job_record=queue_job,
        extra={
            "started_at": started_at.isoformat(),
            "executor": "codex_watcher",
        },
    )

    try:
        run_root, prompt_copy = _prepare_run_copy(queue_job, processed_root)
        job = _build_job_from_queue_record(queue_job, run_root, prompt_copy)
    except FileNotFoundError as exc:
        failure_reason = f"missing prompt file: {exc}"
        _handle_queue_failure(
            queue_job,
            job=None,
            failure_reason=failure_reason,
            retryable=False,
            failure_archive=False,
            failed_root=failed_root,
            max_retries=max_retries,
        )
        return

    try:
        success = run_prompt_job(job)
    except Exception as exc:
        failure_reason = str(exc)
        _handle_queue_failure(
            queue_job,
            job=job,
            failure_reason=failure_reason,
            retryable=False,
            failure_archive=failure_archive,
            failed_root=failed_root,
            max_retries=max_retries,
        )
        return

    if not success:
        failure_reason = "preflight"
        _handle_queue_failure(
            queue_job,
            job=job,
            failure_reason=failure_reason,
            retryable=True,
            failure_archive=failure_archive,
            failed_root=failed_root,
            max_retries=max_retries,
        )
        return

    archived_path = _archive_prompt_file(job, processed_root)
    if archived_path is None:
        failure_reason = "missing running prompt during success archive"
        _handle_queue_failure(
            queue_job,
            job=job,
            failure_reason=failure_reason,
            retryable=False,
            failure_archive=False,
            failed_root=failed_root,
            max_retries=max_retries,
        )
        return

    queue_job = queue_runtime.mark_succeeded(
        queue_job, processed_path=str(archived_path)
    )
    duration = (dt.datetime.utcnow() - started_at).total_seconds()
    _emit_job_event(
        "job.succeeded",
        job_record=queue_job,
        extra={
            "duration": duration,
            "processed_path": str(archived_path),
        },
    )
    _emit_job_event(
        "job.archived",
        job_record=queue_job,
        extra={"archived_path": str(archived_path)},
    )
    JOB_STATES[_job_key(job.inbox_rel)] = STATUS_DONE


def _queue_executor_loop(
    queue_root: Path,
    processed_root: Path,
    failed_root: Path,
    *,
    failure_archive: bool,
    max_retries: int,
    stop_event: threading.Event,
) -> None:
    while not stop_event.is_set():
        job_record = queue_runtime.get_next_queued_job(queue_root)
        if job_record is None:
            time.sleep(POLL_INTERVAL_SECONDS)
            continue
        try:
            _process_queue_job(
                job_record,
                processed_root=processed_root,
                failed_root=failed_root,
                failure_archive=failure_archive,
                max_retries=max_retries,
            )
        except Exception as exc:
            log(f"Queue executor error: {exc!r}")


def _drain_queue_once(
    queue_root: Path,
    processed_root: Path,
    failed_root: Path,
    *,
    failure_archive: bool,
    max_retries: int,
) -> None:
    idle_cycles = 0
    while idle_cycles < 3:
        job_record = queue_runtime.get_next_queued_job(queue_root)
        if job_record is None:
            idle_cycles += 1
            time.sleep(0.1)
            continue
        idle_cycles = 0
        try:
            _process_queue_job(
                job_record,
                processed_root=processed_root,
                failed_root=failed_root,
                failure_archive=failure_archive,
                max_retries=max_retries,
            )
        except Exception as exc:
            log(f"Queue executor (once) error: {exc!r}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: Optional[list] = None) -> int:
    global CONFIG, INBOX_MODE

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single pass over the inbox and exit (no watcher).",
    )
    args = parser.parse_args(argv)

    CONFIG, pv_root = load_config()
    INBOX_MODE = CONFIG.get("inbox_mode", "legacy_single_owner")
    JOB_STATES.clear()

    queue_cfg = CONFIG.get("queue", {})
    queue_enabled = bool(queue_cfg.get("enabled"))
    max_retries = int(queue_cfg.get("max_retries", 3))
    failure_archive = bool(queue_cfg.get("failure_archive", False))
    queue_root = _queue_root_from_config(CONFIG) if queue_enabled else None

    inbox_root = Path(CONFIG["inbox"])
    processed_root = Path(CONFIG["processed"])
    finished_root = Path(CONFIG["finished"])
    failed_root = Path(CONFIG["failed"])
    repos_root = Path(CONFIG["repos_root"])

    inbox_root.mkdir(parents=True, exist_ok=True)
    processed_root.mkdir(parents=True, exist_ok=True)
    finished_root.mkdir(parents=True, exist_ok=True)
    failed_root.mkdir(parents=True, exist_ok=True)
    repos_root.mkdir(parents=True, exist_ok=True)

    if queue_enabled and queue_root is None:
        raise RuntimeError("queue.enabled is true but queue root is undefined")
    if queue_enabled:
        queue_runtime.ensure_jobs_root(queue_root)

    if args.once:
        claim_new_prompts(inbox_root)
        if queue_enabled:
            start_jobs_from_running(
                inbox_root,
                processed_root,
                None,
                queue_enabled=True,
                queue_root=queue_root,
            )
            _drain_queue_once(
                queue_root,
                processed_root,
                failed_root,
                failure_archive=failure_archive,
                max_retries=max_retries,
            )
            return 0

        job_queue: "queue.Queue[Job]" = queue.Queue()
        start_jobs_from_running(
            inbox_root,
            processed_root,
            job_queue,
            queue_enabled=False,
            queue_root=None,
        )

        while not job_queue.empty():
            job = job_queue.get()
            try:
                success = run_prompt_job(job)
            except Exception as exc:  # pragma: no cover
                log(f"Error processing {job.inbox_path}: {exc!r}")
                JOB_STATES[_job_key(job.inbox_rel)] = STATUS_ERROR
                finalize_inbox_prompt(
                    inbox_root=inbox_root,
                    finished_root=finished_root,
                    rel=job.inbox_rel,
                    status=STATUS_ERROR,
                    delay_seconds=0.0,
                )
            else:
                if not success:
                    log(
                        "Git preflight: unrecoverable git error; leaving prompt %s in inbox for retry.",
                        job.inbox_rel,
                    )
                    job_queue.task_done()
                    continue
                JOB_STATES[_job_key(job.inbox_rel)] = STATUS_DONE
                finalize_inbox_prompt(
                    inbox_root=inbox_root,
                    finished_root=finished_root,
                    rel=job.inbox_rel,
                    status=STATUS_DONE,
                    delay_seconds=0.0,
                )
            finally:
                job_queue.task_done()
        return 0

    stop_event = threading.Event()
    job_queue: Optional["queue.Queue[Job]"] = None
    log(f"Starting Codex watcher on {inbox_root}")

    if queue_enabled:
        t = threading.Thread(
            target=_queue_executor_loop,
            args=(queue_root, processed_root, failed_root),
            kwargs={
                "failure_archive": failure_archive,
                "max_retries": max_retries,
                "stop_event": stop_event,
            },
            daemon=True,
        )
    else:
        job_queue = queue.Queue()
        t = threading.Thread(target=worker, args=(job_queue, stop_event), daemon=True)

    t.start()

    def _handle_sigterm(signum, frame):  # pragma: no cover
        log(f"Received signal {signum}, stopping watcher...")
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    try:
        while not stop_event.is_set():
            claim_new_prompts(inbox_root)
            start_jobs_from_running(
                inbox_root,
                processed_root,
                job_queue,
                queue_enabled=queue_enabled,
                queue_root=queue_root,
            )
            time.sleep(POLL_INTERVAL_SECONDS)
    finally:
        stop_event.set()
        t.join()

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
