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

### Inbox, processed, and finished trees

Watcher uses three top-level directories:

- `inbox`  \
  Where `.prompt.md` files are dropped for execution. The watcher will:

  - Claim new prompts by renaming `xyz.prompt.md` → `xyz.running.md`.
  - Run Codex using the `.running.md` path.
  - After completion, rename the file to `xyz.done.md` or `xyz.error.md` and
    then move it into `finished`.

- `processed`  \
  Per-run working directory where Codex output, logs, and PR artifacts are stored.

- `finished`  \
  Archive of completed prompts. The layout mirrors `inbox`:

  ```text
  finished/<repo>/<branch>/<filename>.(done|error).md
  ```

The default paths are:

- inbox: /srv/prompt-valet/inbox
- processed: /srv/prompt-valet/processed
- finished: /srv/prompt-valet/finished

Watcher only treats *.prompt.md as new jobs. Files with .running.md,
.done.md, or .error.md suffixes are considered status markers and will
not be re-enqueued.
