# Prompt Valet — Phase 1 Roadmap (Revised)

## Phase Goal
Standardize the filesystem and configuration layout, document the current architecture, and prepare for the upcoming rename of `watcher.yaml` → `prompt-valet.yaml`.

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
- Rename `watcher.yaml` → `prompt-valet.yaml`.
- Move into canonical location (`/srv/prompt-valet/config/`).
- Update watcher + tree-builder scripts to consume the new name.
- Add improved logging of config path and key settings.
