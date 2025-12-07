# Prompt Valet Roadmap (Phase 2)

## Phase 2 — Installer & Deployment

### P2·C1 — Installer Spec & Contract (Unified A/B/C)
- **Analysis (Block A)** — completed; `docs/analysis/P2-C1-analysis.md` captures the current repo state, installer assumptions, and next-step mapping.
- **Implementation (Block B)** — completed; `docs/installer_contract.md` now records the authoritative contract that downstream phases must follow.
- **Verification (Block C)** — pending; formatting/lint checks will run after this file is committed to ensure the written contract is technically clean.

### P2·C2 — Non-interactive installer
- **Analysis (Block A)** — completed; `docs/analysis/P2-C2-analysis.md` captures the current repo state, installer requirements, and the new environment contract.
- **Implementation (Block B)** — completed; `install_prompt_valet.sh`, the systemd unit templates, and `docs/installer_contract.md` were rewritten to match the implementation.
- **Verification (Block C)** — completed; `bash -n install_prompt_valet.sh` and `systemd-analyze verify prompt-valet-watcher.service prompt-valet-tree-builder.service prompt-valet-tree-builder.timer copyparty.service` both succeed, with only the unrelated `netplan-ovs-cleanup.service` permission warning in the systemd output.

### P2·C2+ — TBD
- Further phases will extend the installer and add the TUI described in Phase 3.
