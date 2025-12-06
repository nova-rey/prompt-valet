### Runtime Behavior Change — Repo Reset

Watcher now discards all uncommitted changes and untracked files on each run via `git reset --hard origin/main`. Any local-only modifications must be committed or stashed manually before restarting the service. All Codex-generated edits flow through PRs, so no runtime-local changes should ever be required.
