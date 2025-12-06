### Runtime Behavior Change — Repo Reset

Watcher now discards all uncommitted changes and untracked files on each run via `git reset --hard origin/main`. Any local-only modifications must be committed or stashed manually before restarting the service. All Codex-generated edits flow through PRs, so no runtime-local changes should ever be required.

### Repo resolution per prompt

Watcher now resolves the target Git repository for **each prompt** based on its inbox path and the configured `repos_root`.

- Supported inbox layouts:
  - New: `inbox_root/<owner>/<repo>/<branch>/.../<prompt>.md`
  - Legacy: `inbox_root/<repo>/<branch>/.../<prompt>.md` (owner taken from `git_owner`)
- Derived repo root: `repos_root/<owner>/<repo>` (must contain `.git`).
- For every prompt before execution, watcher will:
  1. Verify the derived repo is a Git repo.
  2. Run `git fetch origin`.
  3. Run `git reset --hard origin/main`.

If the derived repo is missing or not a Git repository, prompt execution fails fast instead of guessing a location.

### Inbox Layout Modes

Watcher can operate in two modes for inbox → repo resolution, controlled by `inbox_mode`:

- **`legacy_single_owner`** (default):  
  Designed for a single Git owner. Prompts are placed under:

  ```text
  inbox/<repo>/<branch>/.../<prompt>.md
  ```

  The owner is taken from git_owner, and repos are cloned under:

  ```text
  repos_root/<git_owner>/<repo>
  ```

- **`multi_owner`**:
  Designed for multi-tenant setups. Prompts are placed under:

  ```text
  inbox/<owner>/<repo>/<branch>/.../<prompt>.md
  ```

  The owner is taken from the inbox path, and repos live at:

  ```text
  repos_root/<owner>/<repo>
  ```

If a prompt path does not match the expected shape for the active mode, watcher logs the reason and skips the file rather than attempting a best-effort guess.
