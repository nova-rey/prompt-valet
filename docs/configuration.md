# Configuration

### Inbox / Repo Layout

Watcher supports two relative layouts under `inbox_root`:

1. **New layout (explicit owner):**

   `inbox_root/<owner>/<repo>/<branch>/.../<prompt>.md`

2. **Legacy layout (no owner):**

   `inbox_root/<repo>/<branch>/.../<prompt>.md`

In layout (2), the owner is taken from `git_owner` in the configuration (required
only for this mode).

The corresponding Git repo root is:

`repos_root/<owner>/<repo>`

Watcher derives `<owner>` and `<repo>` from the prompt path, verifies that
`repos_root/<owner>/<repo>/.git` exists, synchronizes it with:

- `git fetch origin`
- `git reset --hard origin/main`

and then executes Codex in that repo.

### Owner and inbox mode

Watcher supports two inbox layouts, controlled by `inbox_mode`:

- `legacy_single_owner` (default)

  - Inbox layout: `<repo>/<branch>/.../<prompt>.md`
  - Owner is taken from `git_owner`.
  - If `git_owner` is not set at the top level, it is derived from
    `watcher.git_default_owner`.

- `multi_owner`

  - Inbox layout: `<owner>/<repo>/<branch>/.../<prompt>.md`
  - Owner is taken from the first path segment.

In both modes, the Git clone root is:

```text
repos_root/<owner>/<repo>
```

`load_config()` normalizes the top-level keys `git_owner`, `git_host`, and
`inbox_mode` from the watcher section when they are not explicitly set, so
existing configs that use `git_default_owner` and `git_default_host` continue to
work.

If the inbox path does not match the expected layout for the current
`inbox_mode`, the watcher logs a clear error and skips that prompt instead of
guessing.
