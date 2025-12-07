# Prompt Valet Installer Contract (Phase 2 · Checkpoint 2)

The non-interactive installer defined in this phase is `install_prompt_valet.sh`. It must lay down the runtime layout described by the Phase 1 docs, deploy the watcher/tree-builder scripts, and register the systemd services/units that keep Prompt Valet alive.

## Filesystem & layout obligations
- Create `/srv/prompt-valet/{inbox,processed,finished,config,scripts,logs}` and `/srv/repos`, even if they already exist (use `mkdir -p`).
- Drop `codex_watcher.py` and `rebuild_inbox_tree.py` from this repository into `/srv/prompt-valet/scripts/` with executable permissions.
- Clone or refresh the Prompt Valet repository into `/srv/repos/prompt-valet` so operators can inspect the codebase from a stable path.
- When `PV_FILE_SERVER_MODE=copyparty`, ensure `/srv/prompt-valet/logs/copyparty` exists so the service has a log sink.

## Dependencies & runtime prerequisites
1. Run `apt-get update` and install `git`, `python3`, `python3-venv`, `python3-pip`, `systemd`, and `curl` in non-interactive mode.
2. When the Copyparty file server is enabled, install it via `python3 -m pip install --upgrade copyparty` so `/usr/bin/copyparty` is available.
3. The installer must not prompt for input; any missing dependency should stop execution with a clear error unless `PV_VALIDATE_ONLY=1`, in which case the script only prints planned actions.

## Configuration contract (`/srv/prompt-valet/config/prompt-valet.yaml`)
The installer must write a YAML file that mirrors the Phase 1 schema. The file always contains:
```yaml
inbox: "<PV_INBOX_DIR>"
processed: "<PV_PROCESSED_DIR>"
finished: "<PV_FINISHED_DIR>"
repos_root: "<PV_REPOS_DIR>"

tree_builder:
  eager_repos: false
  branch_mode: "all"
  branch_whitelist: []
  branch_blacklist: []
  branch_name_blacklist:
    - "HEAD"
  scan_interval_seconds: 60
  greedy_inboxes: false

watcher:
  auto_clone_missing_repos: true
  cleanup_non_git_dirs: true
  git_default_owner: "<PV_GIT_OWNER>"
  git_default_host: "<PV_GIT_HOST>"
  git_protocol: "<PV_GIT_PROTOCOL>"
  runner_cmd: "<PV_RUNNER_CMD>"
  runner_extra: "<PV_RUNNER_EXTRA>"
  runner_model: "gpt-5.1-codex-mini"
  runner_sandbox: "danger-full-access"
```
All placeholder paths must expand to the environment variable values listed below (e.g., `PV_INBOX_DIR`).

## Copyparty integration (optional)
- When `PV_FILE_SERVER_MODE=copyparty`, emit `/srv/prompt-valet/copyparty.yaml` with a server block that listens on `PV_FILE_SERVER_PORT`, serves `PV_INBOX_DIR` read-only, and writes logs under `$PV_LOGS_DIR/copyparty`.
- The installer must also write `copyparty.service` under `/etc/systemd/system/`, enable it, and start it. When the mode is `none`, the service file must be removed (if present) and `copyparty.service` must be stopped, disabled, and masked.

## Systemd service & timer contract
| Unit | ExecStart | Restart | Notes |
| --- | --- | --- | --- |
| `prompt-valet-watcher.service` | `/usr/bin/env python3 /srv/prompt-valet/scripts/codex_watcher.py` | `on-failure` | `WorkingDirectory=/srv/prompt-valet`; requires the watcher script and config to exist before enabling. |
| `prompt-valet-tree-builder.service` | `/usr/bin/env python3 /srv/prompt-valet/scripts/rebuild_inbox_tree.py` | oneshot | Enabled so the timer can start it; the installer should also invoke it once via `systemctl start` to prime the inbox tree. |
| `prompt-valet-tree-builder.timer` | N/A (timer) | N/A | `OnCalendar=*:0/5`; `Unit=prompt-valet-tree-builder.service`; enabled and started by the installer. |
| `copyparty.service` | `/usr/bin/env copyparty serve --root /srv/prompt-valet/inbox --port <PV_FILE_SERVER_PORT> --log-dir /srv/prompt-valet/logs/copyparty --readonly` | `on-failure` | Only written/enabled when Copyparty mode is active; otherwise the unit is removed and masked. |

After dropping or removing units, the installer must run `systemctl daemon-reload`, enable the watcher and timer units (with `--now` where applicable), and ensure the timer is running so the tree builder service continues to fire.

## Environment & runtime variables
| Name | Purpose | Default |
| --- | --- | --- |
| `PV_GIT_OWNER` | Git owner/repo namespace for `prompt-valet` | `nova-rey` |
| `PV_GIT_HOST` | Git host (e.g., `github.com`) | `github.com` |
| `PV_GIT_PROTOCOL` | Protocol to use when cloning (`https` or `ssh`) | `https` |
| `PV_FILE_SERVER_MODE` | File server strategy (`copyparty` or `none`) | `copyparty` |
| `PV_FILE_SERVER_PORT` | Copyparty listen port | `3923` |
| `PV_INBOX_DIR` | Inbox tree root | `/srv/prompt-valet/inbox` |
| `PV_PROCESSED_DIR` | Processed tree root | `/srv/prompt-valet/processed` |
| `PV_FINISHED_DIR` | Finished tree root | `/srv/prompt-valet/finished` |
| `PV_CONFIG_DIR` | Config directory | `/srv/prompt-valet/config` |
| `PV_SCRIPTS_DIR` | Scripts directory | `/srv/prompt-valet/scripts` |
| `PV_LOGS_DIR` | Logs directory | `/srv/prompt-valet/logs` |
| `PV_BASE_DIR` | Prompt Valet base | `/srv/prompt-valet` |
| `PV_REPOS_DIR` | Repository clone root | `/srv/repos` |
| `PV_RUNNER_CMD` | Command used to invoke Codex | `codex` |
| `PV_RUNNER_EXTRA` | Additional flags appended to `PV_RUNNER_CMD` | `` |
| `PV_VALIDATE_ONLY` | Dry-run flag (`0` or `1`) | `0` |

## Behavioral guarantees
- The installer must be idempotent: rerunning without changes should not fail, it should re-clone/update the repo, rewrite configs, and reapply the same systemd units.
- Respect `PV_VALIDATE_ONLY=1` by printing each planned action instead of mutating files, installing packages, or touching `systemctl`.
- If cloning fails, the script should exit non-zero so that the operator is aware the environment is not safe for the watcher.

## Verification checklist
1. Run `bash -n install_prompt_valet.sh` to ensure the script is syntactically valid.
2. Use `systemd-analyze verify` (or equivalent) on each `.service`/`.timer` file before installation to ensure the unit syntax is correct.
3. Confirm `/srv/prompt-valet/config/prompt-valet.yaml` follows the schema above after running the installer.
4. Verify `copyparty.service` is only enabled when `PV_FILE_SERVER_MODE=copyparty` and that the service is disabled/masked otherwise.
5. Document any divergence from these rules and resolve it before declaring Block C complete.
