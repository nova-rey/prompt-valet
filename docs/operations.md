### Runtime Behavior Change — Repo Reset

Watcher now discards all uncommitted changes and untracked files on each run via `git reset --hard origin/main`. Any local-only modifications must be committed or stashed manually before restarting the service. All Codex-generated edits flow through PRs, so no runtime-local changes should ever be required.

### Repo resolution per prompt

Watcher now resolves the target Git repository for **each prompt** based on its inbox path and the configured `repos_root`.

- Expected inbox layout: `inbox_root/<git_owner>/<repo_name>/.../<prompt>.md`.
- Derived repo root: `repos_root/<git_owner>/<repo_name>` (must contain `.git`).
- For every prompt before execution, watcher will:
  1. Verify the derived repo is a Git repo.
  2. Run `git fetch origin`.
  3. Run `git reset --hard origin/main`.

If the derived repo is missing or not a Git repository, prompt execution fails fast instead of guessing a location.
