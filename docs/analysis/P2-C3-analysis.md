# Phase 2 · Checkpoint 3 Analysis

## Repository snapshot
- `install_prompt_valet.sh` is the central Phase 2 script; it creates the `/srv/prompt-valet` layout, installs packages, clones `prompt-valet`, drops the Python agents, writes configs, and registers the systemd units.
- `systemd/` mirrors the units that the installer writes (`prompt-valet-watcher.service`, `prompt-valet-tree-builder.*`, `copyparty.service`), so the installer must keep its templated content aligned with these checked-in files.
- `docs/installer_contract.md`, `docs/phase-roadmap.md`, and `docs/Phase_Roadmap.md` currently describe the Phase 2 contract and status for C1/C2, but none mention the idempotency + validation checks we need for C3. This checkpoint will also introduce a new analysis artifact (`docs/analysis/P2-C3-analysis.md`).

## Installer idempotency scan
1. Directory management is already safe: `ensure_directory` wraps `mkdir -p` and is invoked for all required roots before any write occurs.
2. Config generation is deterministic because `write_prompt_valet_config` builds a static dict and forwards it to `yaml.safe_dump`, but the script still requires the `yaml` module, and the installer never installs that dependency. On a fresh Debian host, the config step will exit with "PyYAML is required" before writing anything, so we must ensure `python3-yaml` (or equivalent) is installed before the `write_prompt_valet_config` stage.
3. Repository syncing reruns `git fetch --all --prune` and `git reset --hard origin/HEAD`, which is idempotent provided the remote is reachable and `origin` exists. There is no stateful data appended to the repo path, so repeated runs should keep the repo clean.
4. Systemd unit installation rewrites the unit files and toggles services/timers; the installer already runs `systemctl daemon-reload` and re-enables/enables `--now` each time. The only gap is that the unit templates are also stored under `systemd/`, so any installer change must keep both copies synchronized.

## DRY-RUN coverage
- `PV_VALIDATE_ONLY=1` toggles `DRY_RUN`. Every mutating helper (`ensure_directory`, `install_packages`, `clone_or_update_repo`, `deploy_scripts`, `write_*`, `reload_and_enable_units`) either skips the action or logs it through `maybe_run`, so the script already avoids writes, package installs, git operations, and `systemctl` calls during a dry run.
- The `maybe_run` helper prints `dry-run: ...` for commands that would otherwise execute, and functions like `write_copyparty_config` and `write_service_units` log a `DRY RUN: would ...` message before returning, so operators can see the intent in the logs.
- To make the dry-run output even more traceable, we should reinforce the non-dry-run path with explicit log statements (e.g., "Writing prompt-valet config" or "Generating watcher unit"), so dry-run vs. real-run behavior can be compared easily.

## Validation surface
1. `bash -n install_prompt_valet.sh` currently returns 0 with no diagnostics, so the installer is syntactically valid.
2. The checked-in systemd units each have the required `[Unit]`, `[Service]`, `[Install]`, and `[Timer]` sections, but we still need a reproducible, human-friendly verification step (e.g., `systemd-analyze verify systemd/*.service systemd/*.timer`).
3. The docs describe the current contract (C2) but lack instructions for manually testing dry-run mode (`PV_VALIDATE_ONLY=1`) and verifying the installer on a Debian VM, including what success looks like.

## Improvements to implement for P2·C3
1. Install the `yaml` dependency before `write_prompt_valet_config` runs (e.g., add `python3-yaml` to the `apt-get install` list) so the config step is deterministic and doesn’t raise an import error on repeat executions.
2. Add explicit logging around the creation of the prompt-valet config, Copyparty config, and service units so operators can compare dry-run logs (``dry-run: ...``) with the real install output and confirm idempotent behavior.
3. Expand `docs/installer_contract.md` with a new manual validation section that walks humans through `PV_VALIDATE_ONLY=1` dry runs, full Debian VM installs, `systemd-analyze verify`, and the observable success criteria (directories, configs, enabled units).
4. Update both `docs/phase-roadmap.md` and `docs/Phase_Roadmap.md` with a new P2·C3 section stating that Blocks A/B/C are complete once the above changes and verifications exist.
5. Keep this analysis document as the definitive Block A artifact for C3 so that downstream reviewers understand the idempotency/Dry structure we inspected before implementing the fixes above.
