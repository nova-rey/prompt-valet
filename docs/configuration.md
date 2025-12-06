# Configuration

### Inbox / Repo Layout

Watcher supports two relative layouts under `inbox_root`:

1. **New layout (explicit owner):**

   `inbox_root/<owner>/<repo>/<branch>/.../<prompt>.md`

2. **Legacy layout (no owner):**

   `inbox_root/<repo>/<branch>/.../<prompt>.md`

In layout (2), the owner is taken from `git_owner` in the configuration.

The corresponding Git repo root is:

`repos_root/<owner>/<repo>`

Watcher derives `<owner>` and `<repo>` from the prompt path, verifies that
`repos_root/<owner>/<repo>/.git` exists, synchronizes it with:

- `git fetch origin`
- `git reset --hard origin/main`

and then executes Codex in that repo.
