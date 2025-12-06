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

### `inbox_mode`

Controls how the watcher interprets inbox paths when mapping prompts to Git repositories.

Supported values:

- `legacy_single_owner` (default)

  - Expected inbox layout (relative to `inbox`):

    ```text
    <repo>/<branch>/.../<prompt>.md
    ```

  - The repository owner is taken from `git_owner` in the configuration.
  - The Git clone root is:

    ```text
    <repos_root>/<git_owner>/<repo>
    ```

- `multi_owner`

  - Expected inbox layout:

    ```text
    <owner>/<repo>/<branch>/.../<prompt>.md
    ```

  - The repository owner is taken from the first segment of the path.
  - The Git clone root is:

    ```text
    <repos_root>/<owner>/<repo>
    ```

If the inbox path does not match the expected layout for the current `inbox_mode`,
the watcher logs a clear error and skips that prompt instead of guessing.
