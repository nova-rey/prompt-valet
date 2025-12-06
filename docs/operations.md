### Runtime Behavior Change — Repo Reset

Watcher now discards all uncommitted changes and untracked files on each run via `git reset --hard origin/main`. Any local-only modifications must be committed or stashed manually before restarting the service. All Codex-generated edits flow through PRs, so no runtime-local changes should ever be required.

### `git_repo_path` (required)

`git_repo_path` is the **only** location `codex_watcher` will use for Git operations.

- It must be set to the root of the Git clone that Codex will modify and open PRs against.
- The directory **must** contain a `.git` folder.
- On startup, and before processing any prompts, watcher will:

  1. Verify that `git_repo_path` is a Git repo.
  2. Run `git fetch origin`.
  3. Run `git reset --hard origin/main`.

If `git_repo_path` is missing or not a Git repository, the watcher will log an error and abort prompt execution rather than guessing a path or continuing with an inconsistent state.
