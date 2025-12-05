#!/usr/bin/env python3
"""
codex_watcher.py

Watches the Codex inbox tree and runs Codex jobs when *.prompt.md files appear.

Behavior summary:

- Inbox layout (root configurable via YAML; default: /srv/prompt-valet/inbox):

    /srv/prompt-valet/inbox/<repo_key>/<branch>/<job_id>.prompt.md

  * <repo_key>  : literal repo name (e.g. CrapsSim-Control, Ghost-Instrument)
  * <branch>    : literal branch name (no rewriting); may be used to create
                  or select branches in the local /srv/repos checkout.
  * <job_id>    : arbitrary job identifier; used to name branches / PRs etc.

- On seeing a new *.prompt.md file:
  * Parse repo_key, branch, job_id from the path.
  * Clone or update the corresponding local repo in /srv/repos/<repo_key>.
  * Create a local branch derived from <branch> and <job_id>.
  * Run Codex (via the codex CLI) against the repo with the prompt file.
  * Commit any changes and push a branch / open a PR (future expansion).
  * Move the prompt file into a processed/ tree for auditing.

Configuration:

- Runtime configuration is loaded from a single YAML file:

    /srv/prompt-valet/config/prompt-valet.yaml

  At minimum, this may contain:

    watcher:
      inbox_dir: /srv/prompt-valet/inbox
      processed_dir: /srv/prompt-valet/processed
      auto_clone_missing_repos: true
      git_default_owner: null        # e.g. "nova-rey"
      git_default_host: github.com
      git_protocol: https
      codex_runner_cmd: codex
      codex_model: gpt-5.1-codex-mini
      codex_sandbox: danger-full-access

- If the YAML file is missing or invalid, DEFAULT_CONFIG is used.

Logging:

- On startup, the watcher logs a single config summary line:

    [prompt-valet] loaded config=<path|<defaults>> inbox=<inbox_dir> processed=<processed_dir> git_owner=<owner> git_host=<host> git_protocol=<proto> runner=<cmd>

  This matches rebuild_inbox_tree.py so it's easy to confirm that both scripts
  are reading the same configuration.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileMovedEvent
from watchdog.observers import Observer

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Default configuration & constants
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_PATH = Path("/srv/prompt-valet/config/prompt-valet.yaml")

DEFAULT_CONFIG: Dict[str, Dict[str, Any]] = {
    "watcher": {
        "inbox_dir": "/srv/prompt-valet/inbox",
        "processed_dir": "/srv/prompt-valet/processed",
        "auto_clone_missing_repos": True,
        "git_default_owner": None,  # e.g. "nova-rey"
        "git_default_host": "github.com",
        "git_protocol": "https",
        "codex_runner_cmd": "codex",
        "codex_model": "gpt-5.1-codex-mini",
        "codex_sandbox": "danger-full-access",
    }
}

# Derived / convenience defaults
DEFAULT_INBOX_DIR = Path(DEFAULT_CONFIG["watcher"]["inbox_dir"])
DEFAULT_PROCESSED_DIR = Path(DEFAULT_CONFIG["watcher"]["processed_dir"])
DEFAULT_REPOS_ROOT = Path("/srv/repos")

DEFAULT_RUNNER_CMD = DEFAULT_CONFIG["watcher"]["codex_runner_cmd"]
DEFAULT_MODEL = DEFAULT_CONFIG["watcher"]["codex_model"]
DEFAULT_SANDBOX = DEFAULT_CONFIG["watcher"]["codex_sandbox"]


def log(msg: str) -> None:
    """Simple stdout logger with prefix."""
    print(f"[codex_watcher] {time.strftime('[%Y-%m-%dT%H:%M:%SZ]', time.gmtime())} {msg}", flush=True)


# ---------------------------------------------------------------------------
# YAML config loading
# ---------------------------------------------------------------------------


def _load_yaml_file(path: Path) -> Dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML not available; install 'pyyaml' to use YAML config.")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError("Top-level YAML config must be a mapping.")
    return data


def load_config() -> Tuple[Dict[str, Dict[str, Any]], str]:
    """
    Load configuration from DEFAULT_CONFIG_PATH, merged over DEFAULT_CONFIG.

    Returns:
        (config_dict, source_label)

    Where source_label is either the actual YAML path (as string) if the file
    was successfully loaded, or "<defaults>" if only DEFAULT_CONFIG was used.
    """
    cfg: Dict[str, Dict[str, Any]] = {k: v.copy() for k, v in DEFAULT_CONFIG.items()}
    source_label = "<defaults>"

    if DEFAULT_CONFIG_PATH.is_file():
        try:
            user_cfg = _load_yaml_file(DEFAULT_CONFIG_PATH)
            if isinstance(user_cfg, dict):
                for section, values in user_cfg.items():
                    if section in cfg and isinstance(values, dict):
                        for key, value in values.items():
                            cfg[section][key] = value
            source_label = str(DEFAULT_CONFIG_PATH)
        except Exception as e:
            log(
                f"Warning: Failed to read YAML config at {DEFAULT_CONFIG_PATH}: {e!r}; "
                "using defaults only."
            )
    else:
        log(f"No YAML config at {DEFAULT_CONFIG_PATH}; using defaults only.")

    return cfg, source_label


# ---------------------------------------------------------------------------
# Job context & helpers
# ---------------------------------------------------------------------------


@dataclass
class JobContext:
    repo_key: str
    branch: str
    job_id: str
    prompt_path: Path
    inbox_dir: Path
    processed_dir: Path

    def repo_dir(self) -> Path:
        return DEFAULT_REPOS_ROOT / self.repo_key

    def processed_prompt_path(self) -> Path:
        # Keep a mirror of inbox structure under processed_dir
        return self.processed_dir / self.repo_key / self.branch / f"{self.job_id}.prompt.md"


def parse_prompt_path(root: Path, path: Path) -> Optional[JobContext]:
    """
    Parse a prompt path of the form:

        /inbox/<repo_key>/<branch>/<job_id>.prompt.md

    Returns JobContext or None if the path doesn't match.
    """
    try:
        rel = path.relative_to(root)
    except ValueError:
        return None

    parts = rel.parts
    if len(parts) != 3:
        return None

    repo_key, branch, filename = parts
    if not filename.endswith(".prompt.md"):
        return None

    job_id = filename[: -len(".prompt.md")]
    if not repo_key or not branch or not job_id:
        return None

    return JobContext(
        repo_key=repo_key,
        branch=branch,
        job_id=job_id,
        prompt_path=path,
        inbox_dir=root,
        processed_dir=Path(DEFAULT_CONFIG["watcher"]["processed_dir"]),
    )


def ensure_repo_cloned(ctx: JobContext, cfg: Dict[str, Dict[str, Any]]) -> None:
    """
    Ensure the repo exists under /srv/repos/<repo_key>.

    If it doesn't exist and auto_clone_missing_repos is true, clone it from
    git_default_owner@git_default_host using git_protocol.
    """
    repo_dir = ctx.repo_dir()
    if repo_dir.is_dir():
        log(f"Repo {ctx.repo_key} already cloned at {repo_dir}")
        return

    watcher_cfg = cfg.get("watcher", {})
    auto_clone = bool(watcher_cfg.get("auto_clone_missing_repos", True))
    owner = watcher_cfg.get("git_default_owner")
    host = watcher_cfg.get("git_default_host", "github.com")
    protocol = watcher_cfg.get("git_protocol", "https")

    if not auto_clone:
        raise RuntimeError(
            f"Repo {ctx.repo_key!r} missing at {repo_dir} and auto_clone_missing_repos is false."
        )

    if not owner:
        raise RuntimeError(
            f"Repo {ctx.repo_key!r} missing at {repo_dir} and git_default_owner is not set."
        )

    url: Optional[str]
    if protocol == "ssh":
        url = f"git@{host}:{owner}/{ctx.repo_key}.git"
    elif protocol == "https":
        url = f"https://{host}/{owner}/{ctx.repo_key}.git"
    else:
        raise RuntimeError(f"Unsupported git protocol: {protocol!r}")

    log(f"Cloning repo {ctx.repo_key!r} from {url!r} into {repo_dir}")
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", url, str(repo_dir)],
        check=True,
    )


def git_run(args, cwd: Path) -> None:
    """
    Run a git command, logging stdout/stderr for debugging.
    """
    log(f"RUN: {args!r} (cwd={cwd})")
    result = subprocess.run(
        args,
        cwd=str(cwd),
        text=True,
        capture_output=True,
    )
    if result.stdout:
        log(f"STDOUT:\n{result.stdout.rstrip()}")
    if result.stderr:
        log(f"STDERR:\n{result.stderr.rstrip()}")
    if result.returncode != 0:
        raise RuntimeError(f"Command failed with code {result.returncode}: {args!r}")


def prepare_branch(ctx: JobContext) -> None:
    """
    Ensure the base branch exists and create a job-specific branch:

        <job_branch> = agent/<job_id>

    If the base branch doesn't exist locally, we attempt to fetch it from origin.
    """
    repo_dir = ctx.repo_dir()
    base_branch = ctx.branch
    job_branch = f"agent/{ctx.job_id}"

    # Ensure we have the latest data
    git_run(["git", "fetch", "origin"], cwd=repo_dir)

    # Check out the base branch (or create tracking from origin)
    result = subprocess.run(
        ["git", "rev-parse", "--verify", base_branch],
        cwd=str(repo_dir),
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        # Try origin/<base_branch>
        remote_ref = f"origin/{base_branch}"
        result2 = subprocess.run(
            ["git", "rev-parse", "--verify", remote_ref],
            cwd=str(repo_dir),
            text=True,
            capture_output=True,
        )
        if result2.returncode == 0:
            git_run(["git", "checkout", "-b", base_branch, remote_ref], cwd=repo_dir)
        else:
            raise RuntimeError(
                f"Base branch {base_branch!r} not found locally or as origin/{base_branch!r}."
            )
    else:
        git_run(["git", "checkout", base_branch], cwd=repo_dir)

    # Create the job branch from the base branch
    git_run(["git", "checkout", "-b", job_branch], cwd=repo_dir)


def run_codex(ctx: JobContext, cfg: Dict[str, Dict[str, Any]]) -> None:
    """
    Run Codex against the repo using the prompt file.
    """
    watcher_cfg = cfg.get("watcher", {})
    runner_cmd = watcher_cfg.get("codex_runner_cmd", DEFAULT_RUNNER_CMD)
    model = watcher_cfg.get("codex_model", DEFAULT_MODEL)
    sandbox = watcher_cfg.get("codex_sandbox", DEFAULT_SANDBOX)

    repo_dir = ctx.repo_dir()
    prompt_content: str

    # Read the prompt file so we can at least guard against empty content.
    with ctx.prompt_path.open("r", encoding="utf-8") as f:
        prompt_content = f.read()

    if not prompt_content.strip():
        raise RuntimeError(f"Prompt file {ctx.prompt_path} is empty or whitespace; refusing to run Codex.")

    # Where Codex CLI will dump the final message for debugging/auditing
    last_msg_dir = repo_dir / "docs" / "AGENT_RUNS"
    last_msg_dir.mkdir(parents=True, exist_ok=True)
    last_msg_path = last_msg_dir / f"codex-run-{time.strftime('%Y%m%d-%H%M-%S')}.md"

    # NOTE: We now pass the **path** to the prompt file as the last argument,
    # so codex CLI can handle the .prompt.md semantics itself (YAML front-matter,
    # multi-part sections, etc.), instead of us unwrapping it and shoving the
    # raw text directly into argv.
    cmd = [
        runner_cmd,
        "exec",
        "--skip-git-repo-check",
        "--cd",
        str(repo_dir),
        "--output-last-message",
        str(last_msg_path),
        "--model",
        model,
        "--sandbox",
        sandbox,
        str(ctx.prompt_path),
    ]

    log(f"Running Codex: {cmd!r}")
    result = subprocess.run(
        cmd,
        text=True,
        capture_output=True,
    )
    if result.stdout:
        log(f"Codex STDOUT:\n{result.stdout.rstrip()}")
    if result.stderr:
        log(f"Codex STDERR:\n{result.stderr.rstrip()}")
    if result.returncode != 0:
        raise RuntimeError(f"Codex run failed with code {result.returncode}: {cmd!r}")


def commit_and_push(ctx: JobContext) -> None:
    """
    Commit any changes and push the job branch to origin.

    NOTE: This is intentionally conservative; if there are no changes,
    we still log what happened.
    """
    repo_dir = ctx.repo_dir()
    job_branch = f"agent/{ctx.job_id}"

    # Check for changes
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(repo_dir),
        text=True,
        capture_output=True,
    )
    if status.returncode != 0:
        raise RuntimeError(f"git status failed: {status.stderr}")

    if not status.stdout.strip():
        log(f"No changes detected in {repo_dir}; nothing to commit for job {ctx.job_id!r}.")
        return

    git_run(["git", "add", "-A"], cwd=repo_dir)
    git_run(["git", "commit", "-m", f"Prompt Valet job {ctx.job_id}"], cwd=repo_dir)
    git_run(["git", "push", "-u", "origin", job_branch], cwd=repo_dir)
    log(f"Pushed branch {job_branch!r} for repo {ctx.repo_key!r}.")


def move_to_processed(ctx: JobContext) -> None:
    """
    Move the prompt file to the processed/ tree, mirroring the inbox structure.
    """
    dst = ctx.processed_prompt_path()
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(ctx.prompt_path), str(dst))
    log(f"Moved prompt file to processed: {dst}")


def process_prompt_file(path: Path, cfg: Dict[str, Dict[str, Any]]) -> None:
    """
    Main job handler for a single prompt file.
    """
    inbox_root = Path(cfg["watcher"].get("inbox_dir", str(DEFAULT_INBOX_DIR)))
    processed_root = Path(cfg["watcher"].get("processed_dir", str(DEFAULT_PROCESSED_DIR)))

    ctx = parse_prompt_path(inbox_root, path)
    if ctx is None:
        log(f"Path {path!r} does not look like repo/branch/file, skipping.")
        return

    # Update context with processed_dir from config
    ctx.processed_dir = processed_root

    log(f"Processing job {ctx.job_id!r} for repo {ctx.repo_key!r} branch {ctx.branch!r}")

    # Ensure repo is cloned / updated
    ensure_repo_cloned(ctx, cfg)

    # Prepare job branch
    prepare_branch(ctx)

    # Run Codex on this repo + prompt
    run_codex(ctx, cfg)

    # Commit, push, etc. (current implementation: commit & push only)
    commit_and_push(ctx)

    # Move prompt into processed tree
    move_to_processed(ctx)


# ---------------------------------------------------------------------------
# Watchdog handler & worker thread
# ---------------------------------------------------------------------------


class PromptEventHandler(FileSystemEventHandler):
    """
    Watchdog handler that reacts to *.prompt.md files appearing in the inbox.
    """

    def __init__(self, inbox_root: Path, job_queue: "queue.Queue[Path]") -> None:
        super().__init__()
        self.inbox_root = inbox_root
        self.job_queue = job_queue

    def _maybe_queue_path(self, path: Path) -> None:
        if not path.name.endswith(".prompt.md"):
            return
        # Only queue files that actually live under the inbox root
        try:
            path.relative_to(self.inbox_root)
        except ValueError:
            return
        log(f"Detected new prompt file: {path}")
        self.job_queue.put(path)

    def on_created(self, event) -> None:  # type: ignore[override]
        if isinstance(event, FileCreatedEvent):
            self._maybe_queue_path(Path(event.src_path))

    def on_moved(self, event) -> None:  # type: ignore[override]
        if isinstance(event, FileMovedEvent):
            self._maybe_queue_path(Path(event.dest_path))


def worker_loop(job_queue: "queue.Queue[Path]", cfg: Dict[str, Dict[str, Any]]) -> None:
    """
    Worker thread: process prompt files sequentially.
    """
    while True:
        path = job_queue.get()
        if path is None:  # Sentinel for shutdown (not currently used)
            break
        try:
            process_prompt_file(path, cfg)
        except Exception as e:
            log(f"Error processing {path}: {e}")
        finally:
            job_queue.task_done()


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    cfg, source_label = load_config()
    watcher_cfg = cfg.get("watcher", {})

    inbox_dir = Path(watcher_cfg.get("inbox_dir", str(DEFAULT_INBOX_DIR)))
    processed_dir = Path(watcher_cfg.get("processed_dir", str(DEFAULT_PROCESSED_DIR)))
    git_owner = watcher_cfg.get("git_default_owner")
    git_host = watcher_cfg.get("git_default_host", "github.com")
    git_proto = watcher_cfg.get("git_protocol", "https")
    runner_cmd = watcher_cfg.get("codex_runner_cmd", DEFAULT_RUNNER_CMD)

    # Ensure directories exist (or at least the inbox root)
    os.makedirs(inbox_dir, exist_ok=True)
    os.makedirs(processed_dir, exist_ok=True)

    # Unified startup log line (mirrors rebuild_inbox_tree.py)
    log(
        f"[prompt-valet] loaded config={source_label} "
        f"inbox={inbox_dir} processed={processed_dir} "
        f"git_owner={git_owner} git_host={git_host} git_protocol={git_proto} "
        f"runner={runner_cmd}"
    )

    job_queue: "queue.Queue[Path]" = queue.Queue()

    # Start worker thread
    worker = threading.Thread(target=worker_loop, args=(job_queue, cfg), daemon=True)
    worker.start()

    # Start watchdog observer
    event_handler = PromptEventHandler(inbox_dir, job_queue)
    observer = Observer()
    observer.schedule(event_handler, str(inbox_dir), recursive=True)
    observer.start()

    log(f"Starting Codex watcher on {inbox_dir}")

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        log("Shutting down on KeyboardInterrupt")
    finally:
        observer.stop()
        observer.join()


if __name__ == "__main__":
    main()