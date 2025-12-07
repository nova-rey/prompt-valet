# Prompt Valet Installer Contract (Phase 2 · Checkpoint 2)

This contract anchors the **non-interactive** installer (`install_prompt_valet.sh`) described in Phase 2 · Checkpoint 2. Every subsequent deployment must run that script (or an equivalent automation) so that the runtime environment matches the expectations of the Phase 1 schema and the Codex-driven watcher/tree-builder loop.

## Installer responsibilities
The installer must:

1. Prepare `/srv/prompt-valet/{inbox,processed,finished,config,scripts,logs}` plus `/srv/repos`. The directories must be created with `mkdir -p` so the script can be re-run idempotently.
1. Register the runtime configuration in `/srv/prompt-valet/config/prompt-valet.yaml` by emitting the Phase 1 schema’s `inbox`, `processed`, `finished`, `repos_root`, `tree_builder`, and `watcher` keys (see the `configs/prompt-valet.yaml` template for the expected defaults).
1. Clone or update the Prompt Valet repository (`nova-rey/prompt-valet`) under `$PV_REPOS_DIR/$PV_GIT_OWNER/prompt-valet` using the configured protocol/host/owner, and copy `codex_watcher.py` and `rebuild_inbox_tree.py` from that checkout into `/srv/prompt-valet/scripts/`. The installer must keep the copied files executable.
1. Install dependencies via `apt-get update` + `apt-get install -y git python3 python3-venv systemd curl wget python3-pip python3-yaml`, then install Copyparty via `python3 -m pip install --upgrade copyparty` whenever `PV_FILE_SERVER_MODE=copyparty`.
1. Create `/etc/systemd/system/{prompt-valet-watcher.service,prompt-valet-tree-builder.service,prompt-valet-tree-builder.timer,copyparty.service}` from the repository templates and run `systemctl daemon-reload`.
1. Enable/start the watcher service plus the tree builder service/timer; enable `copyparty.service` only when the file server mode is `copyparty`, otherwise keep that unit disabled/stopped.
1. Respect `PV_VALIDATE_ONLY=1` by printing every planned action, skipping directory creation, Git operations, config deployment, and `systemctl` calls during dry-runs.

## Environment variables
The installer exposes the following knobs with statically documented defaults:

| Variable | Default | Purpose |
| --- | --- | --- |
| `PV_GIT_OWNER` | `nova-rey` | Owner used by the watcher and by the installer when cloning the Prompt Valet repo. |
| `PV_GIT_HOST` | `github.com` | Git host for repository operations. |
| `PV_GIT_PROTOCOL` | `https` | Clone protocol (`https` or `ssh`). |
| `PV_FILE_SERVER_MODE` | `copyparty` | `copyparty` runs the local file server; `none` leaves it disabled (FTP reserved for future phases). |
| `PV_FILE_SERVER_PORT` | `3923` | Port baked into the `copyparty.service` unit; the installer rewrites the systemd file so this port is hard-coded. |
| `PV_INBOX_DIR`/`PV_PROCESSED_DIR`/`PV_CONFIG_DIR`/`PV_SCRIPTS_DIR`/`PV_LOGS_DIR`/`PV_BASE_DIR` | `/srv/prompt-valet/...` | Filesystem roots copied into `prompt-valet.yaml`. |
| `PV_REPOS_DIR` | `/srv/repos` | Root that the watcher/tree-builder inspect for git repositories. |
| `PV_RUNNER_CMD` | `codex` | Command line invoked by the watcher when it runs the prompt. |
| `PV_RUNNER_EXTRA` | *(empty)* | Optional extra flags stored in the generated YAML for operator visibility. |
| `PV_VALIDATE_ONLY` | `0` | When `1`, the installer performs a dry-run: it logs each step but leaves the system untouched. |

## Configuration contract (`prompt-valet.yaml`)
The installer emits `/srv/prompt-valet/config/prompt-valet.yaml` via a Python helper that mirrors the Phase 1 schema:

- `inbox`, `processed`, `finished`, and `repos_root` point at the canonical directories created above.
- `tree_builder` populates `greedy_inboxes`, `branch_mode`, `branch_whitelist`, `branch_blacklist`, `branch_name_blacklist`, `placeholder_branches`, `scan_interval_seconds`, and `eager_repos` with the defaults defined in `configs/prompt-valet.yaml`.
- `watcher` supplies `auto_clone_missing_repos`, `git_default_owner`, `git_default_host`, `git_protocol`, `cleanup_non_git_dirs`, `runner_cmd`, `runner_model`, `runner_sandbox`, and the `runner_extra` string so that downstream code has a faithful record of the invocation arguments.

The file is overwritten on every run so operators can tune the env vars and re-run the installer to refresh the config. The Python generator relies on `python3-yaml` so the package must be available before writing the file.

## Script deployment
`install_prompt_valet.sh` copies `codex_watcher.py` and `rebuild_inbox_tree.py` from the cloned Prompt Valet checkout into `/srv/prompt-valet/scripts/`. The scripts must remain executable (`chmod +x`) so systemd units can run them via `/usr/bin/env python3`. The copy step happens *after* the repo update so the installer always ships the checked-out version of the runtime code.

## Systemd units
All systemd units live under `/etc/systemd/system/`:

1. `prompt-valet-watcher.service`
   ```
   [Unit]
   Description=Prompt Valet — Watcher
   After=network-online.target

   [Service]
   ExecStart=/usr/bin/env python3 /srv/prompt-valet/scripts/codex_watcher.py
   WorkingDirectory=/srv/prompt-valet
   Restart=on-failure

   [Install]
   WantedBy=multi-user.target
   ```

2. `prompt-valet-tree-builder.service`
   ```
   [Unit]
   Description=Prompt Valet — Inbox Tree Builder

   [Service]
   Type=oneshot
   ExecStart=/usr/bin/env python3 /srv/prompt-valet/scripts/rebuild_inbox_tree.py

   [Install]
   WantedBy=multi-user.target
   ```

3. `prompt-valet-tree-builder.timer`
   ```
   [Unit]
   Description=Prompt Valet — Tree Builder Timer

   [Timer]
   OnCalendar=*:0/5
   Unit=prompt-valet-tree-builder.service

   [Install]
   WantedBy=timers.target
   ```

4. `copyparty.service` (optional, controlled by `PV_FILE_SERVER_MODE`)
   ```
   [Unit]
   Description=Prompt Valet — Copyparty File Server
   After=network-online.target

   [Service]
   WorkingDirectory=/srv/prompt-valet
   ExecStart=/usr/bin/env copyparty serve --name "Prompt Valet Inbox" --chdir /srv/prompt-valet/inbox -i 0.0.0.0 -p __PV_FILE_SERVER_PORT__ -v /srv/prompt-valet/inbox:/inbox:r -v /srv/prompt-valet/processed:/processed:r
   Restart=on-failure

   [Install]
   WantedBy=multi-user.target
   ```

   The installer rewrites `__PV_FILE_SERVER_PORT__` with the value of `PV_FILE_SERVER_PORT` when it installs the unit file. The systemd file stays on disk regardless of the file-server mode so that the systemctl state can be cleanly disabled when `PV_FILE_SERVER_MODE=none`.

After copying the unit files, `install_prompt_valet.sh` executes `systemctl daemon-reload` and enables/starts the watcher service, the tree builder service, its timer, and (when enabled) `copyparty.service`. When `PV_FILE_SERVER_MODE=none`, the installer disables/stops `copyparty.service` so no stray HTTP listener remains.

## File server integration
When `PV_FILE_SERVER_MODE=copyparty`, the installer writes `/srv/prompt-valet/copyparty.yaml`:

```
service:
  name: "Prompt Valet Inbox"
  mode: copyparty
  port: <port>
  inbox: /srv/prompt-valet/inbox
  processed: /srv/prompt-valet/processed
```

It also installs/enables `copyparty.service` (which uses the same inbox/processed roots) and installs Copyparty via `pip`. When the file server is disabled, the installer removes `copyparty.yaml` and keeps the service masked.

## Validation & idempotency
- `install_prompt_valet.sh` runs with `set -euo pipefail`, makes no assumptions about the current working directory, and honors `PV_VALIDATE_ONLY=1` to stay read-only.
- The script recalculates and overwrites `prompt-valet.yaml` on every invocation, so the Phase 1 schema keys always reflect the latest environment variables.
- The systemd units are rewritable (copied from repo templates each run), so `systemctl daemon-reload` refreshes them before `systemctl enable --now` is invoked.
- For verification (Block C), the installer run should pass `bash -n install_prompt_valet.sh` and `systemd-analyze verify /etc/systemd/system/*.service /etc/systemd/system/*.timer`. Markdown tooling should confirm that `docs/installer_contract.md` and `docs/analysis/P2-C2-analysis.md` remain well formatted, and the roadmap stays synced with the current checkpoint status.
