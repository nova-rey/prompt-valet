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
