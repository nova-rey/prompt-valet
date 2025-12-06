# Prompt Valet — Phase 1 Roadmap (Revised)

## Phase Goal
Standardize the filesystem and configuration layout, document the current architecture, and lock in `/srv/prompt-valet/config/prompt-valet.yaml` as the canonical runtime config.

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

-### P1·C2 — Config Rename (ABC)
- Ensure `/srv/prompt-valet/config/prompt-valet.yaml` is the canonical runtime config.
- Update watcher + tree-builder scripts to consume that path and log the config path plus key settings.
- [x] Add git preflight guard to Codex watcher (clean tree + `git pull --ff-only`).
