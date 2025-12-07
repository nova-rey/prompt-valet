# Phase 2 · Checkpoint 2 Analysis

## Repository landscape
- `configs/prompt-valet.yaml` already codifies the Phase 1 schema: inbox/processed/repos roots plus `tree_builder`/`watcher` defaults that map directly into `scripts/codex_watcher.py` and `scripts/rebuild_inbox_tree.py`.
- `scripts/` contains the two runtime agents that the installer will distribute under `/srv/prompt-valet/scripts/`. Each script loads `/srv/prompt-valet/config/prompt-valet.yaml`, so the installer must create that file and its directories before services start.
- `docs/` repeatedly references `/srv/prompt-valet` and `/srv/repos` (see `docs/Prompt_Valet_Overview.md`, `docs/Bible.md`, `docs/Architecture.md`, `docs/Config_Spec.md`, etc.), so the installer needs to honour those canonical paths while remaining compatible with the Phase 1 schema.
- The Phase 1 schema itself (mirrored by `configs/prompt-valet.yaml` and `scripts/*`) requires `inbox`, `processed`, `finished`, `repos_root`, `tree_builder`, and `watcher` keys. The installer must preserve those keys and the defaults listed for branch filtering (`branch_mode`, `branch_whitelist`, `branch_blacklist`, `branch_name_blacklist`, `placeholder_branches`, `scan_interval_seconds`, `eager_repos`, `greedy_inboxes`) plus the watcher defaults (`auto_clone_missing_repos`, `git_default_owner/host/protocol`, `cleanup_non_git_dirs`, runner settings).

## Block A takeaways
1. **Filesystem expectations** — `/srv/prompt-valet/{inbox,processed,config,scripts,logs,finished}` plus `/srv/repos` must exist, so mkdir operations must be idempotent.
2. **Dependencies** — the installer is responsible for `apt-get update`/`apt-get install -y git python3 python3-venv systemd` (curl/wget support and Copyparty via pip when needed) and for installing `copyparty` when `PV_FILE_SERVER_MODE=copyparty`.
3. **Repository management** — the installer clones/updates the Prompt Valet repo into `$PV_REPOS_DIR` using the configured owner/host/protocol, then copies `codex_watcher.py` and `rebuild_inbox_tree.py` from that repo into `$PV_SCRIPTS_DIR`.
4. **Config generation** — `prompt-valet.yaml` must be rendered from the Phase 1 schema and include every runtime path; the installer also optionally generates `copyparty.yaml` when the file server mode is `copyparty`.
5. **Systemd surface** — the installer is responsible for the three core units (`prompt-valet-watcher.service`, `prompt-valet-tree-builder.service`, `prompt-valet-tree-builder.timer`) plus `copyparty.service` (only when enabled), ensuring they live in `/etc/systemd/system`, calling `systemctl daemon-reload`, and enabling/starting the correct set of units.
6. **Environment API** — the installer exposes exactly the required env vars so other orchestration can control Git owner/host/protocol, filesystem roots, runner command details, file-server mode/port, and validation mode.
7. **Non‑interactivity & idempotence** — the script must run without prompts, tolerate being re-run, and refrain from mutating the system when `PV_VALIDATE_ONLY=1`.

## Installer environment variable API
| Name | Default | Purpose |
| --- | --- | --- |
| `PV_GIT_OWNER` | `nova-rey` | Git owner used for cloning the Prompt Valet repo and for watcher defaults (`git_default_owner`). |
| `PV_GIT_HOST` | `github.com` | Git host for cloning/watcher operations. |
| `PV_GIT_PROTOCOL` | `https` | Either `https` or `ssh`; controls the clone URL template. |
| `PV_FILE_SERVER_MODE` | `copyparty` | `copyparty` installs the file server; `none` keeps it disabled (FTP still reserved for later). |
| `PV_FILE_SERVER_PORT` | `3923` | Copyparty listens here when enabled; the service file is rewritten at install time so the port is fixed in `/etc/systemd/system/copy...`. |
| `PV_INBOX_DIR` … `PV_LOGS_DIR`/`PV_BASE_DIR`/`PV_REPOS_DIR` | `/srv/prompt-valet/*` and `/srv/repos` | Canonical directories to create and to serialize into `prompt-valet.yaml`. |
| `PV_RUNNER_CMD` | `codex` | Runner program invoked by the watcher. |
| `PV_RUNNER_EXTRA` | (empty) | Placeholder for extra `codex` CLI arguments; persisted into the generated YAML for documentation. |
| `PV_VALIDATE_ONLY` | `0` | When `1`, the installer prints each planned action but makes no system changes (no directories created, no files written, no services started). |

## Files the installer creates/overwrites
- `install_prompt_valet.sh` — the non-interactive Bash installer described above.
- Systemd units: `prompt-valet-watcher.service`, `prompt-valet-tree-builder.service`, `prompt-valet-tree-builder.timer`, and `copyparty.service`.
- Documentation updates: the refreshed `docs/installer_contract.md`, a new `docs/analysis/P2-C2-analysis.md` (this file), and `docs/phase-roadmap.md` to record the Checkpoint 2 status.

## Next steps (Blocks B & C)
- Block B will author the installer script/systemd units, overwrite the installer contract, and mark the roadmap as “Checkpoint 2 in-flight.”
- Block C will run the required validations (`bash -n install_prompt_valet.sh`, `systemd-analyze verify` on the units, markdown linting expectations, `docs/installer_contract.md` vs. actual behavior) and update the roadmap to show P2·C2 complete before delivering the final summary.
