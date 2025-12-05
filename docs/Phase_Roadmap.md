# Prompt Valet — Phase 1 Roadmap (Revised)

## Phase Goal
Standardize the filesystem and configuration layout, document the current architecture, and confirm every component loads `/srv/prompt-valet/config/prompt-valet.yaml`.

## Checkpoints

### P1·C1 — Docs Foundation (Mini-Push)
- Create `/docs` directory.
- Populate seed documentation:
  - Bible.md
  - Design_Philosophy.md
  - Architecture.md
  - Filesystem.md
  - Config_Spec.md
  - Prompt_Valet_Overview.md
  - Phase_Roadmap.md
- Document the actual current behavior as deployed.
- No code edits; documentation only.

### P1·C2 — Config Rename (ABC)
- Canonical config is now `/srv/prompt-valet/config/prompt-valet.yaml`.
- Watcher and tree-builder scripts consume that single YAML file.
- Both scripts emit a startup log line showing the config path plus key watcher settings and directories.
