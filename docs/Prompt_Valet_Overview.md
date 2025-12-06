# Prompt Valet Overview (Seed Version)

Prompt Valet is a local automation system designed to execute Codex-based development prompts in a controlled, reproducible, private environment.

## Why Prompt Valet Exists
The cloud version of Codex automates code generation through a web interface.  
Prompt Valet brings the same workflow fully on-premise:
- No cloud dependency
- Full transparency
- Git-first workflows
- Reproducible runs

## How It Works (Today)
1. User drops a `.md` prompt into `inbox/<repo>/<branch>/`.
2. The watcher detects it.
3. The repo auto-cloner ensures `/srv/repos/<repo>/` exists.
4. The branch tree-builder ensures branch subfolders exist in the inbox.
5. The Codex runner executes the prompt and writes results into the repo.
6. A PR is opened on GitHub.
7. The prompt is moved to the processed directory.

### Worker Repository Hygiene

The Codex runner keeps a local worker clone of each target GitHub repo on the runner host. This clone is **disposable** and treated as an implementation detail:

- Before each run, the watcher ensures the worker repo is a clean checkout of the configured branch.
- If the worker repo has local changes or untracked files, the watcher replaces the working copy entirely with a fresh clone to discard local edits.
- If the repo cannot be repaired (e.g. Git fetch/reset fails), the run is skipped and the error is logged.

Manual edits in the worker clone are unsupported and will be overwritten. The GitHub repository is the single source of truth.

### codex-watcher.service lifecycle

- The systemd service watches `/srv/prompt-valet/inbox` for new prompts.
- For each job, it clones a fresh copy of the target repository into `/srv/repos/<owner>/<name>`, destroying any previous working copy.
- Codex runs against that clean checkout.
- If the run produces changes, the watcher creates a branch, commits, pushes, and opens a GitHub PR via `gh`.
- The prompt file is moved into `processed/` (and then `finished/`) after the run completes.
- There is no separate PR service today; references to `codex-pr.service` are obsolete.

## What Comes Next
- The canonical runtime config is `prompt-valet.yaml` at `/srv/prompt-valet/config/prompt-valet.yaml`
- Phase 2 installer: one-shot deployment
- Phase 3 TUI: configuration wizard and dashboard
- Future: plugin-style extensions and service integrations
