# Phase 2 · Checkpoint 1 Analysis

## Repository snapshot
- `docs/` captures the existing architecture, config, and operational knowledge from Phase 1; key files include `Bible.md`, `Architecture.md`, `Filesystem.md`, `Config_Spec.md`, `Prompt_Valet_Overview.md`, and `Phase_Roadmap.md`.
- `configs/` holds the canonical `prompt-valet.yaml` (tree builder, watcher defaults) plus a legacy `codex-runner.yaml` that mirrors those defaults.
- `scripts/` contains the runtime agents (`codex_watcher.py` and `rebuild_inbox_tree.py`), each wired to `/srv/prompt-valet/config/prompt-valet.yaml` and hard-coded roots under `/srv/prompt-valet/` or `/srv/repos` for the inbox, processed, and repo trees.
- Phase 1 artifacts focus on documentation and config normalization; there is no installer or systemd unit yet, but the operational intent (Codex watcher, tree builder, optional Copyparty file server) is recorded across the docs.

## Installer contract skeleton derived from current behavior
### Explicit assumptions
1. Target host is Debian 12 (or compatible Debian/Ubuntu) so the installer can rely on `apt`, `systemd`, and `python3` in `/usr/bin`.
2. The installer runs as `root` (or via `sudo`) so it can create `/srv/prompt-valet`, drop configs, register systemd units, and manage `/srv/repos` clones.
3. `systemd` is available and used to supervise the watcher and tree-builder services; reboots should restart the needed units.
4. Internet access exists for `apt` package installation and for cloning repositories from Git hosting (default `github.com`).
5. The Codex CLI (`codex`) and `gh` command-line tools must be installed before running the watcher; the installer should verify or install them (e.g., via pip or apt).

### Public environment variables
| Name | Description | Default / Notes |
| PV_RUN_ID | Unique ID supplied to each Codex invocation | Set by the watcher per prompt; no static default |
| PV_RUN_ROOT | Temporary run directory for the current job under `/srv/prompt-valet/processed` | Populated by the watcher; no direct override |
| PV_PROMPT_FILE | Path to the `.prompt.md` file consumed by Codex | Derived from the inbox job and always populated before `codex exec` runs |
| GITHUB_TOKEN / GH_TOKEN | Token used by `gh pr create` when pushing PRs | Must be provided in the environment so `gh` can authenticate unless machine has existing `gh auth login` state; no default value |

### Canonical `/srv/prompt-valet` layout
1. `inbox/` – watcher watches this tree for `<optional owner>/<repo>/<branch>/<...>.prompt.md`. Legacy mode allows `inbox/<repo>/<branch>/...` using a configured single owner.
2. `processed/` – processed prompts and run artifacts (`docs/AGENT_RUNS`, log snippets) are placed here; the watcher creates run-specific subfolders.
3. `finished/` – final resting place for status-tagged prompt files (`.done`, `.error`), mirrored from the inbox via `finalize_inbox_prompt`.
4. `config/` – stores `prompt-valet.yaml` (required). An optional `copyparty.yaml` may live here if the file server is enabled.
5. `scripts/` – contains `codex_watcher.py` and `rebuild_inbox_tree.py`; the installer must place these Python scripts under `/srv/prompt-valet/scripts/` and keep them executable.
6. `logs/` – reserved for future watcher/log outputs.
7. `repos/` (under `/srv/prompt-valet`) – root for cloned git repositories, mirroring `<owner>/<repo>` structure derived from inbox paths.

### Repo cloning and update logic
- `codex_watcher.py` resolves each prompt path to `<owner>/<repo>/<branch>` (legacy mode uses `git_owner` from config).
- Missing repos are auto-cloned if `watcher.auto_clone_missing_repos` is true; the URL template is `<git_protocol>://<git_host>/<owner>/<repo>.git` where the owner defaults to `git_default_owner`.
- Before each run the watcher fetches `origin`, checks out `main`, resets hard to `origin/main`, cleans untracked files (`git clean -fd`), and then prepares/creates the agent branch (`codex/<prompt>-<jobid>`); this ensures runs always use a clean, deterministic base.
- Worker clones are disposable: if `ensure_worker_repo_clean_and_synced` fails the job is skipped and the prompt remains in the inbox for retry.

### Script deployment rules
- `codex_watcher.py` must live under `/srv/prompt-valet/scripts/`, be executed via `python3`, and run either as a long-lived service (tailing the inbox) or in `--once` mode for debugging.
- `rebuild_inbox_tree.py` is a one-shot sync that can be invoked on demand or via a systemd timer; it works against `/srv/repos` and `/srv/prompt-valet/inbox`, respecting the same `tree_builder` config block.
- Both scripts expect `/srv/prompt-valet/config/prompt-valet.yaml` to exist; the installer should create that file before enabling the units and should ensure directories exist before the scripts read them.

### Config generation rules (Phase 1 schema)
- The installer must produce `/srv/prompt-valet/config/prompt-valet.yaml` with the following structure:
- `tree_builder` subsections for `eager_repos`, `branch_mode`, `branch_whitelist`, `branch_blacklist`, `branch_name_blacklist`, and `scan_interval_seconds`.
  - `inbox`, `processed`, `finished`, `repos_root` pointing at the canonical `/srv/prompt-valet` subdirectories (legacy scripts still expect `/srv/repos` so the installer must ensure either symlinks or consistent values for dynamic behavior).
  - `watcher` block for `auto_clone_missing_repos`, `git_default_owner`, `git_default_host`, `git_protocol`, `cleanup_non_git_dirs`, `runner_cmd`, `runner_model`, `runner_sandbox`.
- Defaults should mirror `configs/prompt-valet.yaml`; the installer may expose overrides via command-line flags or interactive prompts but must serialize the final YAML with the documented keys.

### File server integration rules
- Copyparty is the optional file server referenced in `docs/Filesystem.md` and `Bible.md`. If enabled:
  - Install Copyparty binaries (outside this repo) and drop an immutable `copyparty.yaml` configuration under `/srv/prompt-valet/` (Pointing to `/srv/prompt-valet/inbox`, `/srv/prompt-valet/processed`, etc.).
  - The installer should register a `copyparty.service` or enable an existing service that serves the inbox root; otherwise, the installer should document that the file server is intentionally disabled (`none`).
  - Ensure Copyparty’s HTTP port does not conflict with other services (document default port in the contract) and that the inbox path is exported read-only for watchers to safely mutate it.
- FTP is not currently in use but the contract should state the file server integration is optional (`copyparty` or `none`) and that the tree builder/watcher must never rely on an FTP server being present.

### Systemd unit contract
| Unit | ExecStart | Restart policy | Conditions / Notes |
| --- | --- | --- | --- |
| `codex-watcher.service` | `/usr/bin/python3 /srv/prompt-valet/scripts/codex_watcher.py` | `Restart=on-failure`, `RestartSec=5` | `ConditionPathExists=/srv/prompt-valet/scripts/codex_watcher.py`; requires `codex` + `gh` installed; should run as `prompt-valet` user or root with access to `/srv/prompt-valet`.
| `codex-tree-builder.service` | `/usr/bin/python3 /srv/prompt-valet/scripts/rebuild_inbox_tree.py` | `Restart=on-success` (service invoked via timer, hence single-shot) | `ConditionPathExists=/srv/prompt-valet/scripts/rebuild_inbox_tree.py`; no persistent workload — the systemd timer drives scheduling.
| `codex-tree-builder.timer` | N/A (starts the service) | `Unit=codex-tree-builder.service`, `OnCalendar=*-*-* *:*:00` or `OnBootSec=5m` as needed | Ensures the inbox tree stays in sync; the timer should be enabled alongside the service.
| `copyparty.service` (optional) | Command depends on Copyparty packaging (e.g., `/usr/local/bin/copyparty serve /srv/prompt-valet/inbox`) | `Restart=on-failure` | Only enabled when Copyparty is configured; otherwise the service should remain disabled.

### Idempotency and rerun behavior
- `codex_watcher` marks claimed prompts with `.running.md` and, on completion, writes `.done`/`.error` and moves files into `/srv/prompt-valet/finished`, preventing reprocessing of the same prompt path.
- The worker repo is rebuilt before each run (`git fetch`, `git reset --hard`, `git clean -fd`); if cloning or syncing fails, the job is skipped and logged without corrupting the repo.
- `rebuild_inbox_tree` is safe to run repeatedly; it prunes stale inbox branches/repos and re-creates directories only when git reports matching refs.

### Error-handling rules
- The watcher logs every git/Codex command; non-fatal errors (e.g., git push failure) are captured so the process can move on to the next job rather than crashing.
- `create_pr_for_job` swallows errors from git push or `gh pr create` while logging them; the system does not attempt retries inside the same job to keep failure handling simple.
- If a prompt disappears mid-run, a `NO_INPUT.md` file is written inside the run root and the job is treated as a no-op success to avoid repeated failures.
- `rebuild_inbox_tree` writes `ERROR.md` markers when it removes stale folders so operators can diagnose pruning actions.

## Checkpoint outputs and next steps
1. `docs/installer_contract.md` will capture the full spec detailed above so Phase 2 implementation can follow it verbatim.
2. `docs/analysis/P2-C1-analysis.md` (this file) records the current repository state, derived installer behavior, and file mapping.
3. `docs/Phase_Roadmap.md` must be updated to mark P2·C1 as completed (analysis + implementation stage) so downstream phases know the contract exists.

Any remaining details (package requirements, exact systemd unit parameters) will be finalized in Block B when we author the installer contract and clean up `Phase_Roadmap.md`.
