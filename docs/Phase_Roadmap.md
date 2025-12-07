# Prompt Valet Roadmap (Phase 2)

## Phase 2 — Installer & Deployment

### P2·C1 — Installer Spec & Contract (Unified A/B/C)
- **Analysis (Block A)** — completed; `docs/analysis/P2-C1-analysis.md` captures the repo baseline and derived contract assumptions.
- **Implementation (Block B)** — completed; `docs/installer_contract.md` now defines the canonical installer behavior.
- **Verification (Block C)** — completed; formatting/lint checks verified the contract document.

### P2·C2 — Non-interactive Installer Implementation (Unified A/B/C)
- **Analysis (Block A)** — completed; `docs/analysis/P2-C2-analysis.md` records the Phase 2 requirements, directory expectations, and environment API.
- **Implementation (Block B)** — completed; `install_prompt_valet.sh`, the four systemd unit templates, and refreshed docs now encode the deployment steps for the watcher, tree builder, and optional file server.
- **Verification (Block C)** — completed; `bash -n install_prompt_valet.sh`, `systemd-analyze verify` on the units, and `python3 -m markdown` over the updated docs all succeeded (with only the expected netplan warning described in notes).

### P2·C3 — Idempotency, DRY-RUN Hardening, and Validation Surface (Unified A/B/C)
- **Analysis (Block A)** — completed; `docs/analysis/P2-C3-analysis.md` documents the installer's idempotency and PV_VALIDATE_ONLY coverage, the PyYAML dependency gap, and the verification goals for this checkpoint.
- **Implementation (Block B)** — completed; `install_prompt_valet.sh` now installs `python3-yaml`, surface-level logs each config/unit change, and explicitly records both dry-run and real-run actions, while the installer contract (and Phase Roadmap) now describe the human verification workflow for dry runs and Debian VM installs.
- **Verification (Block C)** — completed; `bash -n install_prompt_valet.sh`, `systemd-analyze verify systemd/*.service systemd/*.timer`, and review of the manual validation steps were executed to confirm the idempotency and validation surfaces remain intact after the hardening work.
