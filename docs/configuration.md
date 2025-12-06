# Configuration

### Inbox / Repo Layout

Watcher assumes a mirrored directory layout between the inbox and the Git clones:

- Inbox:
  `inbox_root/<git_owner>/<repo_name>/.../<prompt>.md`
- Repos:
  `repos_root/<git_owner>/<repo_name>/.git`

For each prompt, watcher derives the target repo from the prompt’s inbox path and then:

1. Computes the repo root as `repos_root/<git_owner>/<repo_name>`.
2. Verifies it is a Git repository (`.git` exists).
3. Runs `git fetch origin` and `git reset --hard origin/main` in that repo.
4. Executes Codex with that repo as the working directory.

This makes watcher multi-repo by design, with no hard-coded repo paths.
