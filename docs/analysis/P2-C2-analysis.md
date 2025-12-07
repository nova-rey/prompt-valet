# Phase 2 · Checkpoint 2 Analysis

## Block A findings
- `configs/` already contains `prompt-valet.yaml` defaults for the watcher+tree builder and a legacy `codex-runner.yaml`; these files define the Phase 1 schema that the installer must mirror when it writes `/srv/prompt-valet/config/prompt-valet.yaml`.
- `scripts/` holds the runtime agents (`codex_watcher.py`, `rebuild_inbox_tree.py`) that the installer must copy under `/srv/prompt-valet/scripts/` and keep executable for the services.
- `docs/` catalogs the architecture, filesystem layout, roadmaps, and the Phase 2·C1 contract; the installer implementation must honor those assumptions (create the prescribed directories, clone/update `/srv/repos`, and place systemd units for the watcher, timer, and optional Copyparty server).
- The repository root does not yet provide install scripts or unit files, so Block B must add `install_prompt_valet.sh`, the four `.service/.timer` snapshots, and refreshed documentation.

## Phase 2 installer requirements
1. The installer must create `/srv/prompt-valet/{inbox,processed,finished,config,scripts,logs}` plus `/srv/repos`, install apt dependencies (`git`, `python3`, `python3-venv`, `python3-pip`, `systemd`, `curl`), and optionally install Copyparty via pip when `PV_FILE_SERVER_MODE=copyparty`.
2. It must clone (or update) the Prompt Valet repo into `$PV_REPOS_DIR` and deploy the two Python agents into `$PV_SCRIPTS_DIR` before writing the canonical YAML config.
3. It must generate `/srv/prompt-valet/config/prompt-valet.yaml` that mirrors Phase 1’s schema (tree builder + watcher subsections, canonical paths pointing at `/srv/prompt-valet` plus `/srv/repos`, and defaults derived from `configs/prompt-valet.yaml`).
4. When the file server mode is `copyparty`, it must also emit `/srv/prompt-valet/copyparty.yaml`, install `copyparty.service`, and ensure the Copyparty log directory under `$PV_LOGS_DIR` exists.
5. Systemd units (`prompt-valet-watcher.service`, `prompt-valet-tree-builder.service`, `prompt-valet-tree-builder.timer`, `copyparty.service` when enabled) must be written into `/etc/systemd/system/`, followed by `systemctl daemon-reload`, enabling/starting the units, and disabling/masking Copyparty when the mode is `none`.
6. The installer must be fully non-interactive, idempotent, and respect `PV_VALIDATE_ONLY=1` by logging planned actions instead of mutating the host.

## Environment API
| Variable | Description | Default |
| --- | --- | --- |
| `PV_GIT_OWNER` | Git owner that hosts Prompt Valet | `nova-rey` |
| `PV_GIT_HOST` | Git host | `github.com` |
| `PV_GIT_PROTOCOL` | Clone protocol (`https` or `ssh`) | `https` |
| `PV_FILE_SERVER_MODE` | File server mode | `copyparty` |
| `PV_FILE_SERVER_PORT` | Copyparty listening port | `3923` |
| `PV_INBOX_DIR` | Inbox root | `/srv/prompt-valet/inbox` |
| `PV_PROCESSED_DIR` | Processed root | `/srv/prompt-valet/processed` |
| `PV_FINISHED_DIR` | Finished root | `/srv/prompt-valet/finished` |
| `PV_CONFIG_DIR` | Config directory | `/srv/prompt-valet/config` |
| `PV_SCRIPTS_DIR` | Script directory | `/srv/prompt-valet/scripts` |
| `PV_LOGS_DIR` | Logs directory | `/srv/prompt-valet/logs` |
| `PV_BASE_DIR` | Prompt Valet base | `/srv/prompt-valet` |
| `PV_REPOS_DIR` | Repo clones root | `/srv/repos` |
| `PV_RUNNER_CMD` | Codex command | `codex` |
| `PV_RUNNER_EXTRA` | Extra runner flags | `` |
| `PV_VALIDATE_ONLY` | Dry-run guard (`0` or `1`) | `0` |

## Next steps
- Block B must author the installer script and systemd units while updating `docs/installer_contract.md` and `docs/phase-roadmap.md` to reflect the implementation work.
- Block C will verify the script (`bash -n`), validate the unit files, ensure markdown/docs stay aligned, and mark the roadmap entry as complete before producing the final report.
