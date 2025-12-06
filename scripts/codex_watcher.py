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
import datetime as dt
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Literal, Optional, Tuple

import yaml  # type: ignore

try:
    from watchdog.events import FileSystemEventHandler, FileSystemEvent
    from watchdog.observers import Observer
except ImportError as exc:  # pragma: no cover
    print(f"[codex_watcher] watchdog is required: {exc}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Config & constants
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_PATH = Path("/srv/prompt-valet/config/prompt-valet.yaml")

DEFAULT_CONFIG: Dict[str, Any] = {
    "inbox": "/srv/prompt-valet/inbox",
    "processed": "/srv/prompt-valet/processed",
    "finished": "/srv/prompt-valet/finished",
    "repos_root": "/srv/prompt-valet/repos",
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


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def run_git(args, cwd: Path, allow_failure: bool = False) -> subprocess.CompletedProcess:
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
    base_branch: str = "main",
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
    run_git(["fetch", "origin"], cwd=repo_dir)
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
        stderr = e.stderr.decode(errors="ignore") if getattr(e, "stderr", None) else str(e)
        print("[codex_watcher] ERROR: Git synchronization failed.")
        print(stderr)
        raise RuntimeError("Git synchronization failed; aborting prompt execution.") from e


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

    return cfg


def load_config() -> Dict[str, Any]:
    cfg = DEFAULT_CONFIG.copy()
    path = DEFAULT_CONFIG_PATH

    loaded_path: str = "<defaults>"
    if path.is_file():
        try:
            user_cfg = yaml.safe_load(path.read_text()) or {}
            if not isinstance(user_cfg, dict):
                raise ValueError("YAML config is not a mapping at top level")
            # shallow merge; nested dicts we expect to be dicts
            for key, value in user_cfg.items():
                if (
                    isinstance(value, dict)
                    and isinstance(cfg.get(key), dict)
                ):
                    cfg[key].update(value)  # type: ignore
                else:
                    cfg[key] = value
            loaded_path = str(path)
        except Exception as exc:  # pragma: no cover
            log(
                f"Failed to load YAML config at {path}: {exc}; "
                "falling back to defaults"
            )
            loaded_path = "<defaults>"

    cfg = normalize_config(cfg)

    inbox_root = Path(cfg["inbox"]).expanduser().resolve()
    processed_root = Path(cfg["processed"]).expanduser().resolve()
    finished_root = Path(cfg.get("finished", DEFAULT_CONFIG["finished"])).expanduser().resolve()
    repos_root = Path(cfg["repos_root"]).expanduser().resolve()
    watcher_cfg = cfg.get("watcher", {})

    inbox_root.mkdir(parents=True, exist_ok=True)
    processed_root.mkdir(parents=True, exist_ok=True)
    finished_root.mkdir(parents=True, exist_ok=True)
    repos_root.mkdir(parents=True, exist_ok=True)

    cfg["inbox"] = str(inbox_root)
    cfg["processed"] = str(processed_root)
    cfg["finished"] = str(finished_root)
    cfg["repos_root"] = str(repos_root)

    log(
        "[prompt-valet] loaded config="
        f"{loaded_path} "
        f"inbox={inbox_root} "
        f"processed={processed_root} "
        f"finished={finished_root} "
        f"repos_root={repos_root} "
        f"git_owner={cfg.get('git_owner')} "
        f"git_host={cfg.get('git_host')} "
        f"git_protocol={watcher_cfg.get('git_protocol', 'https')} "
        f"runner={watcher_cfg.get('runner_cmd')}"
    )

    return cfg


def load_config_from_dict(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize an existing config dict (primarily for tests)."""
    normalized = DEFAULT_CONFIG.copy()
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
    inbox_rel: Path
    inbox_path: Path


class InboxHandler(FileSystemEventHandler):
    """
    Watches for new *.prompt.md files anywhere under the inbox root.
    """

    def __init__(self, inbox_root: Path, job_queue: "queue.Queue[Job]") -> None:
        super().__init__()
        self.inbox_root = inbox_root
        self.job_queue = job_queue

    def _maybe_enqueue(self, path: Path) -> None:
        if not path.name.endswith(".prompt.md"):
            return

        try:
            rel = path.relative_to(self.inbox_root)
        except ValueError:
            return

        try:
            git_owner, repo_name, branch_name, _, _ = resolve_prompt_repo(
                CONFIG, str(path)
            )
        except RuntimeError as exc:
            print(
                f"[codex_watcher] Skipping prompt {path}: "
                f"unable to derive repo root ({exc})."
            )
            return

        try:
            running_path = claim_inbox_prompt(self.inbox_root, rel)
        except FileNotFoundError:
            print(
                f"[prompt-valet] Warning: prompt file {rel} disappeared "
                "before it could be claimed; skipping."
            )
            return

        job = Job(
            git_owner=git_owner,
            repo_name=repo_name,
            branch_name=branch_name,
            inbox_rel=rel,
            inbox_path=running_path,
        )
        log(f"Detected new prompt file: {running_path}")
        self.job_queue.put(job)

    def on_created(self, event: FileSystemEvent) -> None:  # pragma: no cover
        if event.is_directory:
            return
        self._maybe_enqueue(Path(event.src_path))

    def on_moved(self, event: FileSystemEvent) -> None:  # pragma: no cover
        if event.is_directory:
            return
        self._maybe_enqueue(Path(event.dest_path))


def run_codex_for_job(repo_dir: Path, job: Job, run_root: Path, run_id: str) -> None:
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
    prompt_path = Path(job.inbox_path)

    env.update(
        {
            "PV_RUN_ID": run_id,
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
        raise RuntimeError(
            f"Codex CLI failed with code {proc.returncode}: {cli_cmd!r}"
        )


def process_job(job: Job) -> None:
    """
    Run a single job end-to-end:
    - make sure repo is cloned
    - prepare job branch
    - run Codex
    - (Codex prompt is responsible for commits/PRs)
    """
    inbox_root = Path(CONFIG["inbox"])
    processed_root = Path(CONFIG["processed"])
    repos_root = Path(CONFIG["repos_root"]).expanduser().resolve()

    original_prompt_path = inbox_root / job.inbox_rel
    repo_dir = derive_repo_root_from_prompt(CONFIG, str(original_prompt_path))
    ensure_repo_cloned(repos_root, job.git_owner, job.repo_name)

    run_git_sync(str(repo_dir))

    job_branch = job.branch_name
    prepare_branch(repo_dir, job_branch, base_branch="main")

    run_id = dt.datetime.utcnow().strftime("%Y%m%d-%H%M-%S")
    run_root = processed_root / job.git_owner / job.repo_name / job.branch_name / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    prompt_path = run_root / "prompt.md"

    inbox_running = Path(job.inbox_path)
    if inbox_running.exists():
        shutil.copy2(inbox_running, prompt_path)
        prompt_exists = True
    else:
        log(
            "[prompt-valet] Warning: prompt file missing at inbox path, "
            "continuing with no-op Codex run."
        )
        prompt_exists = False

    log(
        "[prompt-valet] "
        f"run={run_id} repo={job.git_owner}/{job.repo_name} branch={job.branch_name} "
        f"prompt_inbox={inbox_running} prompt_copy={prompt_path} processed={run_root}"
    )

    if prompt_exists:
        run_codex_for_job(repo_dir, job, run_root, run_id)
    else:
        no_input = run_root / "NO_INPUT.md"
        no_input.write_text(
            "This run started without a prompt file. Likely the prompt "
            "referenced inbox paths or moved itself. Execution continued "
            "safely.\n"
        )


def worker(job_queue: "queue.Queue[Job]", stop_event: threading.Event) -> None:
    """
    Worker loop: pull jobs from queue and process serially.
    """
    inbox_root = Path(CONFIG["inbox"])
    finished_root = Path(CONFIG["finished"])

    while not stop_event.is_set():
        try:
            job = job_queue.get(timeout=1.0)
        except queue.Empty:
            continue

        try:
            log(f"Processing job {job!r}")
            process_job(job)
        except Exception as exc:
            log(f"Error processing {job.inbox_path}: {exc!r}")
            finalize_inbox_prompt(
                inbox_root=inbox_root,
                finished_root=finished_root,
                rel=job.inbox_rel,
                status=STATUS_ERROR,
            )
        else:
            finalize_inbox_prompt(
                inbox_root=inbox_root,
                finished_root=finished_root,
                rel=job.inbox_rel,
                status=STATUS_DONE,
            )
        finally:
            job_queue.task_done()


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

    CONFIG = load_config()
    INBOX_MODE = CONFIG.get("inbox_mode", "legacy_single_owner")

    inbox_root = Path(CONFIG["inbox"])
    processed_root = Path(CONFIG["processed"])
    finished_root = Path(CONFIG["finished"])

    inbox_root.mkdir(parents=True, exist_ok=True)
    processed_root.mkdir(parents=True, exist_ok=True)
    finished_root.mkdir(parents=True, exist_ok=True)
    Path(CONFIG["repos_root"]).expanduser().resolve().mkdir(parents=True, exist_ok=True)

    job_queue: "queue.Queue[Job]" = queue.Queue()
    stop_event = threading.Event()

    if args.once:
        # Simple scan and exit: enqueue any existing *.prompt.md files
        for path in inbox_root.rglob("*.prompt.md"):
            if not path.is_file():
                continue

            rel = path.relative_to(inbox_root)

            try:
                git_owner, repo_name, branch_name, _, _ = resolve_prompt_repo(
                    CONFIG, str(path)
                )
            except RuntimeError as exc:
                print(
                    f"[codex_watcher] Skipping prompt {path}: "
                    f"unable to derive repo root ({exc})."
                )
                continue

            try:
                running_path = claim_inbox_prompt(inbox_root, rel)
            except FileNotFoundError:
                print(
                    f"[prompt-valet] Warning: prompt file {rel} disappeared "
                    "before it could be claimed; skipping."
                )
                continue

            job_queue.put(
                Job(
                    git_owner=git_owner,
                    repo_name=repo_name,
                    branch_name=branch_name,
                    inbox_rel=rel,
                    inbox_path=running_path,
                ),
            )

        # Process synchronously
        while not job_queue.empty():
            job = job_queue.get()
            try:
                process_job(job)
            except Exception as exc:  # pragma: no cover
                log(f"Error processing {job.inbox_path}: {exc!r}")
                finalize_inbox_prompt(
                    inbox_root=inbox_root,
                    finished_root=finished_root,
                    rel=job.inbox_rel,
                    status=STATUS_ERROR,
                    delay_seconds=0.0,
                )
            else:
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

    # Normal watcher mode
    handler = InboxHandler(inbox_root, job_queue)
    observer = Observer()
    observer.schedule(handler, str(inbox_root), recursive=True)
    observer.start()
    log(f"Starting Codex watcher on {inbox_root}")

    t = threading.Thread(target=worker, args=(job_queue, stop_event), daemon=True)
    t.start()

    def _handle_sigterm(signum, frame):  # pragma: no cover
        log(f"Received signal {signum}, stopping watcher...")
        stop_event.set()
        observer.stop()

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    try:
        while observer.is_alive():
            observer.join(timeout=1.0)
    finally:
        stop_event.set()
        observer.stop()
        observer.join()

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())