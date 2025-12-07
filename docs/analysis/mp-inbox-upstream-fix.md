# MINI-PUSH ANALYSIS — Inbox Validity, Config, Upstream

## Config loading today

- `scripts/rebuild_inbox_tree.py` loads config from `DEFAULT_CONFIG_PATH` (`/srv/prompt-valet/config/prompt-valet.yaml`) and merges it into `DEFAULT_CONFIG`.
- The watcher section defines `git_default_owner`, `git_default_host`, `git_protocol`, plus the runner/token settings: these are the knobs that control whether upstream discovery can talk to GitHub.
- If the file is absent, `load_config()` logs `No config file at <path>, using defaults.` and silently keeps the default watcher keys (`git_default_owner="owner"`, `git_default_host="github.com"`, `git_protocol="https"`). There is no `upstream_enabled` flag or warning about running without config; tree building just carries on with the defaults.
- Every run ends with `[prompt-valet] loaded config=... git_owner=... git_host=... git_protocol=... runner=<none>`, so the current message doesn’t distinguish “config applied” vs “default fallbacks”.

## Current repo discovery & validity logic

- `discover_repos(REPOS_ROOT)` walks `/srv/repos` and yields bare/local repositories to seed `local_repos`.
- `repo_keys` starts with the local repo names and then adds any existing directory under `/srv/prompt-valet/inbox`, so the rebuild pass touches both clones and pre-created inbox roots.
- For `repo_key` names that fail `is_valid_repo_key`, `mark_inbox_root_invalid()` is invoked with a reason about illegal characters, and `_write_repo_error()` drops an `ERROR.md` without deleting the folder.
- Any names not associated with a local clone proceed to `reconcile_upstream_repo()`, which:
  - Calls `check_upstream_repo()` → `git ls-remote` against the URL built from watcher config.
  - If GitHub says “missing repo,” calls `mark_inbox_root_invalid(... "does not exist upstream")`.
  - Otherwise syncs branches and calls `mark_inbox_root_valid()` to clear stale error markers.
- There is no other branch that marks an inbox root invalid, so “valid/invalid” today depends solely on repo-key syntax and upstream existence.

## Missing config / upstream failure impact

- Without config, defaults are still used to build remote URLs, so the script still “phones home” to GitHub with owner `owner`, host `github.com`, etc.
- If upstream discovery fails (network/auth), the default path logs `Upstream check failed ... leaving inbox untouched.` and returns without taking action, so failures already do not flip validity flags.
- The cleanup logic for “no local clone” is therefore currently tied to upstream discovery and still invalidates roots for `repo_missing_from_stderr`. There is no explicit logging that says “Cleaning inbox roots that do not map to real repos” in this file today, but test expectations suggest such behavior exists or is expected elsewhere; it is effectively “repo missing upstream → mark invalid”. This is what needs to be refactored to keep invalidation tied only to invalid repo-key syntax.

## Plan for the mini-push

1. **Config loading + upstream flag**
   - Keep a single `CONFIG_PATH` (e.g. default `DEFAULT_CONFIG_PATH`, overridable by env) and log explicitly whether it was loaded or missing.
   - Introduce `upstream_enabled` (or similar) that’s `True` only when a user config file exists and parsing succeeds; otherwise log the “local-only mode” warning and skip upstream-specific work.
   - Ensure the startup log mentions the path, owner/host/protocol, and whether upstream discovery is enabled.

2. **Upstream calling behavior**
   - Wrap `reconcile_upstream_repo()` calls behind `upstream_enabled`.
   - When config exists, continue building the URL from watcher settings and let upstream discovery run as before (using mocks in tests). If `git ls-remote` fails, log a warning and leave the inbox root untouched.
   - When upstream is disabled, skip calling `check_upstream_repo()` and log something like `[rebuild_inbox_tree] No config at ...; running in local-only mode.`

3. **Decouple inbox validity from local clones**
   - Remove any `mark_inbox_root_invalid()`/`reset`/`ERROR.md` writes that happen purely because the repo isn’t in `/srv/repos` (i.e., `reconcile_upstream_repo` should no longer invalidate when registry lookup fails).
   - Ensure the only invalidations left are for repo-key syntax problems.
   - Keep warning logs for “missing local clone” or “failed upstream check” but do **not** treat them as errors.

4. **Testing updates**
   - Adjust `tests/test_rebuild_inbox_tree_upstream.py` to assert that `nova-process` (valid repo-key, no local clone) survives without `ERROR.md` whether config is missing or upstream fails.
   - Add mocks/fixtures to exercise:
     - Config present → upstream discovery invoked using the configured owner.
     - Config missing → confirm the “local-only mode” log message and no upstream calls.
     - Syntax invalid repo key → still marked invalid.
   - Ensure upstream errors produce warnings but no `ERROR.md`.

5. **Validation**
   - Run `pytest -q`.
   - Reformat touched files with `black`, lint with `ruff`.
