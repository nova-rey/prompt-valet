# Prompt Valet Filesystem Layout (Seed Version)

This document describes the **current** filesystem layout used by Prompt Valet before Phase 1 performs naming normalization.
```

/srv/prompt-valet/

inbox/

processed/

config/

prompt-valet.yaml # YAML-based canonical config at /srv/prompt-valet/config/prompt-valet.yaml

copyparty.yaml

scripts/

codex_watcher.py

rebuild_inbox_tree.py

logs/

/srv/repos/
```
## Notes
- inbox and processed form the primary user-facing workflow.
- `scripts/` contains the runtime code called by systemd units.
- `config/` will become the canonical home for all Prompt Valet configuration.
- Future phases will add:
  - installer configs
  - TUI state
  - version manifests
