# Phase 2 Debrief & Integration Notes

## Phase 2 snapshot
Phase 2 delivered the unified installer story we scoped in Phase 1: the canonical contract (P2·C1), the deterministic, non-interactive `install_prompt_valet.sh` script plus its systemd units (P2·C2), and the idempotency, DRY RUN, and validation hardening (P2·C3). With this document, we capture the final debrief, human install prescriptions, integration notes for Phases 3 and 4, and the explicit declaration that Phase 2 is now closed.

## Appliance model & installer behavior
- The appliance is the host running Debian (or a compatible derivative) with `/srv/prompt-valet` as the shared root for inbox, processed, finished, config, scripts, and logs plus `/srv/repos` for cloned repositories.
- `install_prompt_valet.sh` is deterministic, non-interactive, idempotent, and driven entirely by the documented environment variables (`PV_*`). It creates the layout, installs dependencies, clones/updates `prompt-valet`, deploys `codex_watcher.py` and `rebuild_inbox_tree.py`, writes `/srv/prompt-valet/config/prompt-valet.yaml`, optionally writes `/srv/prompt-valet/copyparty.yaml`, and manages the systemd units listed in `systemd/*.service` and `systemd/*.timer`.
- The script respects `PV_VALIDATE_ONLY=1` (dry run) by logging each planned action and leaving directories, packages, and units untouched; rerunning without changes rewrites the same files and re-applies the same units so the installer is safe to run repeatedly.

## Running a dry run on a Debian VM
1. Boot or provision a Debian 12 (or compatible) VM and install `bash`, `coreutils`, and the environment you will use for the installer.
2. Copy or clone this repository and `cd` into `/srv/prompt-valet` or your working path.
3. Export the necessary environment overrides (`PV_FILE_SERVER_MODE`, `PV_GIT_OWNER`, etc.) if you want to deviate from the defaults. Always set `PV_VALIDATE_ONLY=1`:
   ```sh
   export PV_VALIDATE_ONLY=1
   sudo ./install_prompt_valet.sh
   ```
4. Confirm the script prints `dry-run:`/`DRY RUN:` prefixes for each action (creating directories, `apt-get` commands, git operations, file writes, and `systemctl` invocations) and does not mutate `/srv/prompt-valet`, `/srv/repos`, or `/etc/systemd/system`.
5. Review the logged plan and adjust environment overrides before the real install if needed.

## Running a full install on a Debian VM
1. Ensure the VM has network access to `apt` repositories and git hosts, and that you can run `sudo` or are `root`.
2. Export any overrides you need (`PV_FILE_SERVER_MODE`, `PV_GIT_HOST`, `PV_RUNNER_CMD`, etc.) and unset `PV_VALIDATE_ONLY` or set it to `0`.
3. Execute the installer as root:
   ```sh
   sudo ./install_prompt_valet.sh
   ```
4. Wait for the script to finish; it will install dependencies (`git`, `python3`, `python3-venv`, `python3-pip`, `python3-yaml`, `systemd`, `curl`), clone/update the repo, drop the Python agents, emit the config files, and register/reload the systemd units.
5. If `PV_FILE_SERVER_MODE=copyparty`, ensure the installer also writes `/srv/prompt-valet/copyparty.yaml` and enables `copyparty.service`.

## Validation & success criteria for real installs
- `bash -n install_prompt_valet.sh` returns exit code 0 (syntax check).
- `systemd-analyze verify systemd/*.service systemd/*.timer` reports no errors before enabling the units.
- `systemctl daemon-reload` is part of the installer and should finish without errors.
- `systemctl is-enabled prompt-valet-watcher.service`, `prompt-valet-tree-builder.service`, and `prompt-valet-tree-builder.timer` all report `enabled`.
- `systemctl status prompt-valet-watcher.service` shows the watcher is running; the timer should show `Active: active (waiting)` and the tree-builder service should show `Active: inactive (dead)` but with recent `Start` entries.
- When `PV_FILE_SERVER_MODE=copyparty`, `copyparty.service` is enabled and running; when the mode is `none`, the service should be disabled and masked.
- The success criteria checklist for a VM install:
  1. `/srv/prompt-valet/{inbox,processed,finished,config,scripts,logs}` and `/srv/repos` exist with the expected permissions.
  2. `/srv/prompt-valet/scripts/` contains `codex_watcher.py` and `rebuild_inbox_tree.py` with executable bits set.
  3. `/srv/prompt-valet/config/prompt-valet.yaml` matches the schema defined in the installer contract (points to the canonical directories and honors overrides).
  4. `systemd` units in `/etc/systemd/system/` match the templates from `systemd/*.service`/`*.timer` and the services/timer are enabled/active per the mode.
  5. Logs referenced under `/srv/prompt-valet/logs/` (and `/srv/prompt-valet/logs/copyparty` when enabled) are writable by the service.

## Expected state after installation
- Directories: `/srv/prompt-valet/inbox`, `processed`, `finished`, `config`, `scripts`, `logs`, plus `/srv/repos` for clone targets.
- Configs: `prompt-valet.yaml` with the tree builder and watcher blocks, `copyparty.yaml` when required, and any override files you added explicitly.
- Repository: `/srv/repos/prompt-valet` exists and is in sync with `origin/main` using the configured protocol/host.
- Systemd units: `prompt-valet-watcher.service`, `prompt-valet-tree-builder.service`, `prompt-valet-tree-builder.timer`, and `copyparty.service` (if applicable) are present in `/etc/systemd/system/`, reloaded, and enabled.
- State: the watcher is running, the timer is waiting to fire every five minutes, and the tree builder service can be triggered manually via `systemctl start prompt-valet-tree-builder.service` if needed.

## Troubleshooting notes
- If `apt-get install` fails, rerun `sudo apt-get update` and ensure the repository lists are reachable; check `/var/log/apt/term.log` for clues.
- Missing Python dependencies (PyYAML) manifest as `ModuleNotFoundError`; manually install `python3-yaml` and re-run the installer, which is safe because of idempotency.
- `systemctl status` and `journalctl -u prompt-valet-watcher.service` are the primary places to inspect runtime errors; rerun `systemctl daemon-reload` if unit files change.
- If directories are absent or permissions wrong, rerun the installer (the script always uses `mkdir -p` and `chmod`), or fix the directory and rerun the script; it will add missing pieces without duplicating work.
- For Copyparty issues, confirm `PV_FILE_SERVER_MODE` matches the intended state (`copyparty` vs `none`) and that `/srv/prompt-valet/logs/copyparty` exists.

## Phase 3 Wizard & Phase 4 TUI integration
- The Wizard and TUI each wrap `install_prompt_valet.sh`; they set or preview the documented environment variables (`PV_*`) and then invoke the same installer script so the behavior remains deterministic and centralized.
- They never reimplement package installation, config serialization, or systemd wiring—those are the installer’s responsibilities, which makes the higher-level interfaces thin orchestrators that simply export env vars (including `PV_VALIDATE_ONLY=1` for previews) and run `./install_prompt_valet.sh`.
- Because the installer is idempotent, repeated invocations from the Wizard or TUI (for example, when validating configuration changes) are safe and provide the same checksumable layout every time.

## CI & validation posture
Automated validation remains static; CI does **not** run the installer on a live VM. Instead, CI verifies syntax with `bash -n install_prompt_valet.sh`, runs `systemd-analyze verify systemd/*.service systemd/*.timer`, and checks that the markdown documentation renders cleanly. Real installs and systemd activation are left to manual Debian VMs following the steps above.

## Phase 2 provenance summary (C1 → C4)
1. **P2·C1 (Installer Spec & Contract):** Phase 1 artifacts guided the analysis of the registry, layout, and unit assumptions. `docs/installer_contract.md` now encodes the deterministic behavior, environment API, and systemd contract that P2·C2 implements.
2. **P2·C2 (Non-interactive Installer):** `install_prompt_valet.sh`, the systemd units under `systemd/`, and the updated docs executed the contract by installing dependencies, copying scripts, generating configs, and deploying watching services.
3. **P2·C3 (Idempotency & Validation):** Added PyYAML, logging around config/unit writes, DRY RUN coverage, and a validation workflow for `PV_VALIDATE_ONLY=1`, Debian installs, and `systemd-analyze verify` so humans can see the safety guarantees before moving forward.
4. **P2·C4 (Debrief & Integration):** This document, the new `docs/analysis/P2-C4-analysis.md`, and the roadmap updates close Phase 2 by capturing the manual instructions, success criteria, integration notes, and the Provenance summary that future phases will rely on.

## Phase 2 Closed
Phase 2 is now **closed**. The deterministic installer, idempotency hardening, and validation surface are documented, the human instructions are available, and the hand-off to Phase 3 Wizard (and later Phase 4 TUI) is settled on the shared installer interface.
