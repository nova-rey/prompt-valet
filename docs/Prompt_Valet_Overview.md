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

### Git Safety Before Each Run

Before Codex is allowed to edit the target repository, the watcher performs a git
preflight check:

- The working tree must be completely clean (no modified or untracked files).
- `git fetch origin` and `git pull --ff-only` must succeed.

If either condition fails, the watcher logs an error and skips that prompt without
touching the repo. Fix the git state (commit, reset, or resolve divergence) and
then re-enqueue the prompt.

## What Comes Next
- The Phase 1 rename of `watcher.yaml` → `prompt-valet.yaml`
- Phase 2 installer: one-shot deployment
- Phase 3 TUI: configuration wizard and dashboard
- Future: plugin-style extensions and service integrations
