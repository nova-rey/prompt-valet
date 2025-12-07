# Prompt Valet Installer Contract (Phase 2 · Checkpoint 1)

The installer contract for Phase 2 describes how the system must be laid down so that the Codex-driven automation loop defined in Phase 1 can operate from `/srv/prompt-valet`. The deliverable is a documentation-only phase; no installer code is written yet, but this contract is the authoritative checklist for subsequent implementation (Phase 2·C2+).

## Scope
- Prepare the filesystem layout described below under `/srv/prompt-valet` and `/srv/repos`.
- Drop the runtime Python scripts (`codex_watcher.py`, `rebuild_inbox_tree.py`) into `/srv/prompt-valet/scripts`.
- Generate `/srv/prompt-valet/config/prompt-valet.yaml` that mirrors the Phase 1 schema.
- Ensure dependencies are installed (`python3`, `git`, `codex`, `gh`, optional Copyparty).
- Register systemd units that supervise the watcher, tree builder, and optional file server.
- Do **not** implement the installer shell script or systemd units in this checkpoint; only document the contract.

## Environment & dependency assumptions
1. Debian 12 (or an Ubuntu 22.04+ derivative) is the base OS so `apt`/`systemd`/`python3` behave predictably.
2. The installer runs with root privileges (or via `sudo`) to create `/srv/prompt-valet`, manage `/srv/repos`, adjust permissions, and register systemd units.
3. `systemd` is available and manages long-lived services; reboots should auto-restart the watcher/timer units.
4. Network access exists for `apt install`, `pip`, and `git clone (https://github.com, or other configured host)` operations.
5. The Codex CLI (`codex`) and GitHub CLI (`gh`) are installed before or by the installer; if `gh` is used for PR creation, the user must provide `GH_TOKEN`/`GITHUB_TOKEN` or run `gh auth login` afterward.

## Canonical filesystem layout
| Path | Description |
| --- | --- |
| `/srv/prompt-valet/inbox` | Primary watch tree where user `.prompt.md` files arrive. Supports `inbox/<repo>/<branch>/...` (legacy single owner) and `inbox/<owner>/<repo>/<branch>/...` (multi-owner) depending on `inbox_mode`.
| `/srv/prompt-valet/processed` | Run artifacts, `docs/AGENT_RUNS` copies, and temporary job folders are created here for each prompt.
| `/srv/prompt-valet/finished` | Post-processed prompt files (`.done`, `.error`) are mirrored here after being renamed inside the inbox.
| `/srv/prompt-valet/config` | Houses `prompt-valet.yaml` (required). Optional Copyparty configs reside here as well.
| `/srv/prompt-valet/scripts` | Contains `codex_watcher.py` and `rebuild_inbox_tree.py`; the installer must preserve file permissions and shebangs.
| `/srv/prompt-valet/logs` | Reserved for watcher/tree builder logs produced after this phase.
| `/srv/prompt-valet/copyparty.yaml` | Optional Copyparty configuration when the file server is enabled.
| `/srv/prompt-valet/repos` | Default git clone root; the watcher resolves repos as `/srv/prompt-valet/repos/<owner>/<repo>`.
| `/srv/repos` | Legacy path that some scripts (e.g., `rebuild_inbox_tree.py` defaults) still reference; installer must either symlink `/srv/repos` to `/srv/prompt-valet/repos` or configure the scripts accordingly.

## Configuration contract (`prompt-valet.yaml`)
- The installer must emit `/srv/prompt-valet/config/prompt-valet.yaml` with these top-level keys (referencing `configs/prompt-valet.yaml` in this repo for defaults):
  - `inbox`, `processed`, `finished`, `repos_root`: absolute paths pointing to the directories listed above.
  - `tree_builder`: governs branch discovery.
    - `branch_mode` (`string`, one of `all|whitelist|blacklist|both`): controls which branches are advertised.
    - `branch_whitelist` / `branch_blacklist` (`list[str]`): used in conjunction with `branch_mode`.
    - `branch_name_blacklist` (`list[str]`): branch names never treated as real refs (e.g., `HEAD`).
    - `scan_interval_seconds` (`int`, default `60`).
    - `eager_repos` (`bool`, default `false`): when `true`, create inbox roots for each repo even without existing prompts.
  - `watcher`: controls repo auto-cloning and Codex invocation.
    - `auto_clone_missing_repos` (`bool`, default `true`).
    - `git_default_owner`, `git_default_host`, `git_protocol` (default `https`).
    - `cleanup_non_git_dirs` (`bool`, default `true`).
    - `runner_cmd` (`codex`), `runner_model` (`gpt-5.1-codex-mini`), `runner_sandbox` (`danger-full-access`).
  - Additional keys (`git_owner`, `inbox_mode`) may appear to support legacy single-owner setups.
- The file must be valid YAML, human-editable, and include comments that mirror the defaults in `configs/prompt-valet.yaml` so operators understand each toggle.

## Script deployment & runtime contract
1. Copy `codex_watcher.py` and `rebuild_inbox_tree.py` from this repository into `/srv/prompt-valet/scripts/` and ensure they are executable (or rely on `/usr/bin/python3` to run them).
2. Both scripts read `/srv/prompt-valet/config/prompt-valet.yaml`; the installer must create the directory and file before enabling systemd units that start them.
3. `codex_watcher.py` runs continuously (daemon/service mode). It:
   - Claims `.prompt.md` files, renaming them `.running.md` while processing.
   - Resolves `<owner>/<repo>/<branch>` using `INBOX_MODE` and `git_owner` as documented.
   - Auto-clones missing repos via `<git_protocol>://<git_host>/<owner>/<repo>.git` when allowed.
   - Ensures the worker checkout is clean by fetching `origin`, resetting `main`, cleaning untracked files, and then checking out/creating the agent branch.
   - Invokes `codex exec` with `--skip-git-repo-check`, `--cd` into the repo, `--output-last-message`, `--model` and `--sandbox` pulled from config, while exporting `PV_RUN_ID`, `PV_RUN_ROOT`, and `PV_PROMPT_FILE` into the Codex process.
   - After Codex runs, stages changes, creates a branch, pushes to `origin`, and uses `gh pr create` to open a PR.
   - Marks completed prompts as `.done` or `.error` and moves them into `/srv/prompt-valet/finished`.
4. `rebuild_inbox_tree.py` is one-shot/daemon-managed; it:
   - Scans `/srv/prompt-valet/repos` (or `/srv/repos` depending on config) for git repos.
   - Lists remote branches via `git branch -r`, filters them via the configured tree builder policy, and mirrors them under `inbox/<repo>/<branch>`.
   - Removes stale inbox branches/repos and writes `ERROR.md` markers for removed roots.
   - Can be safely re-run repeatedly without causing data loss.

## Repository cloning & update workflow
- For every prompt, the watcher resolves the repo root via `resolve_prompt_repo`, ensuring the owner, repo, and branch parts exist.
- `ensure_repo_cloned` will auto-clone if a repository directory is absent, using `git clone` over the configured protocol, owner, and host.
- Worker clones are disposable: if the repo is dirty or missing `.git`, the watcher will remove it and re-clone (when `cleanup_non_git_dirs` is `true`).
- After cloning, the watcher fetches `origin`, resets `main` (`git reset --hard origin/main`), cleans (`git clean -fd`), and checks out/creates the job branch.
- If git operations fail, the watcher logs the error, leaves the prompt for retry, and does not mark it as `.done`.

## Public environment variables
| Variable | Purpose | Default / Requirements |
| --- | --- | --- |
| `PV_RUN_ID` | Unique token for the current Codex job | Set by the watcher; not user-configurable.
| `PV_RUN_ROOT` | Directory under `/srv/prompt-valet/processed` containing run artifacts | Set by the watcher to help Codex and downstream logging understand context. |
| `PV_PROMPT_FILE` | Path to the prompt file passed to `codex exec` | Derived from the inbox path; the installer must guarantee the watcher sets it before launching Codex. |
| `GITHUB_TOKEN` / `GH_TOKEN` | Auth token used by `gh pr create` when pushing PRs | Installer should document how the operator populates this variable (or rely on `gh auth login`). |

## Systemd service & timer contract
| Unit | Type | ExecStart | Restart policy | Notes |
| --- | --- | --- | --- | --- |
| `codex-watcher.service` | `service` | `/usr/bin/python3 /srv/prompt-valet/scripts/codex_watcher.py` | `Restart=on-failure`, `RestartSec=5` | Runs as root (or dedicated `prompt-valet` user). `ConditionPathExists` should guard the script and config file. `Requires=network-online.target` ensures git/gh network access.
| `codex-tree-builder.service` | `oneshot` | `/usr/bin/python3 /srv/prompt-valet/scripts/rebuild_inbox_tree.py --once` (or the default invocation) | `Restart=no` (timer restarts the service) | This service is awakened by the timer; it may log or write `ERROR.md` when cleaning branches.
| `codex-tree-builder.timer` | `timer` | N/A | N/A | `OnCalendar=*-*-* *:*:00` (or similar) with `Persistent=true`; `Unit=codex-tree-builder.service` ensures periodic syncing.
| `copyparty.service` (optional) | `service` | Whatever command launches Copyparty (e.g., `/usr/local/bin/copyparty serve /srv/prompt-valet/inbox`) | `Restart=on-failure` | Enabled only when the installer is told to expose the inbox via HTTP.

## File server integration rules
- The file server is optional (`copyparty` or `none`). If enabled:
  - Place Copyparty’s configuration under `/srv/prompt-valet/copyparty.yaml` and ensure it points at `/srv/prompt-valet/inbox` and `/srv/prompt-valet/processed` so operators can see prompt/file status.
  - The service must not modify the inbox/processed directories in ways that interfere with the watcher (read-only or carefully synchronized writes). Prefer launching Copyparty as an unprivileged user.
  - Document the HTTP port and authentication model in the installer output.
- If the operator chooses not to deploy a file server, the installer should simply leave the service masked/disabled and note `file_server=none` in the log/config summary.

## Idempotency & error handling guidelines
- Re-running the watcher service is safe because it atomically claims `.prompt.md` files, writes status files, and settles them in `finished/` so duplicates are ignored.
- Codex runs write `docs/AGENT_RUNS` within each repo; the watcher creates the directory per run if necessary.
- Failure scenarios (missing repo, git errors, Codex crash) are logged and cause the watcher to skip the job, leaving the prompt in the inbox for later retries.
- The tree builder cleans stale branches/repos by deleting directories and emitting `ERROR.md`, giving operators a trace of what changed.
- The installer should ensure log/stdout output is captured (systemd journald is acceptable) so operators can diagnose failures.

## Verification checklist for this contract
1. Directory tree under `/srv/prompt-valet` matches the canonical layout with all required subdirectories.
2. `/srv/prompt-valet/config/prompt-valet.yaml` passes YAML linting and contains the documented keys.
3. Both scripts run (`python3 codex_watcher.py --once`, `python3 rebuild_inbox_tree.py --help`) and exit cleanly when config is sane.
4. The systemd units are linked/enabled but not started until dependencies (config, scripts) exist.
5. Optional Copyparty service is configurable and can be masked when not in use.
6. The contract mentions all public environment variables, repo cloning rules, systemd requirements, and failure handling strategies.
