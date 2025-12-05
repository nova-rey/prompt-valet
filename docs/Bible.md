# Prompt Valet Bible (Seed Version)

This document serves as the long-term historical and architectural record of Prompt Valet.  
It captures how the system is structured today, how it behaves, and how it evolves over time.

## Purpose of Prompt Valet
Prompt Valet is a local orchestration system designed to replace the Codex Cloud “drop a file, get a PR” workflow.  
It uses a watcher, a branch tree-builder, a repo auto-cloner, and a local Codex runner to automate development tasks inside git repositories.

The core loop is:

1. A Markdown prompt file is dropped into the **inbox**.
2. If the repo does not exist under `/srv/repos/`, Prompt Valet auto-clones it.
3. The tree-builder ensures branch folders exist and are mirrored.
4. The Codex watcher executes the prompt using the local Codex runner.
5. The results are committed and submitted as a pull request to the upstream repository.

## Current Components

### Watcher (codex_watcher.py)
- Monitors inbox folders inside `/srv/prompt-valet/inbox`.
- Launches Codex runner jobs.
- Moves processed prompts to `/srv/prompt-valet/processed`.

### Tree Builder (rebuild_inbox_tree.py)
- Scans repos under `/srv/repos/`.
- Generates per-branch folders inside inbox.
- Validates repo existence, branch status, and repo errors.

### Repo Auto-Cloner
- Automatically clones missing repos into `/srv/repos/`.
- Uses owner/host/protocol defaults defined in the config file.

### File Server (Copyparty)
- Optional component used to provide a web interface to the inbox.
- Isolated configuration in `copyparty.yaml`.

## Canonical Paths (Phase 1 Pre-Rename State)
These represent the **current** actual deployment before P1·C2 normalizes names:
```
/srv/prompt-valet/

inbox/

processed/

config/

watcher.yaml # will be renamed in P1·C2

copyparty.yaml

scripts/

codex_watcher.py

rebuild_inbox_tree.py

logs/

/srv/repos/
```
## Notes on Evolution
This document will expand as Prompt Valet gains an installer, a TUI, and a standard extension surface.

It will always record:
- What changed.
- Why it changed.
- How the system should be understood.
