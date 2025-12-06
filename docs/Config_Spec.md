# Prompt Valet Configuration Specification (Seed Version)

This file documents the configuration **as it exists today** in the repo.

The agent must inspect the current `watcher.yaml` in the repo and populate:

1. Top-level sections
2. Supported keys
3. Current defaults
4. Any comments or notes present in the actual file

The purpose of this file is to reflect **current truth**, not the future rename.

## Agent Instructions
- Scan the config file currently used by the scripts.
- Reproduce its structure accurately.
- Do not invent keys not present in the repo.
- This file must reflect the state prior to P1·C2.

## Current Configuration Source
- Path: `configs/codex-runner.yaml`
- Format: YAML with comments that describe the behavior enforced by the watcher and tree-builder.

## Top-Level Sections
### `tree_builder`
- `greedy_inboxes` (bool, default: `false`)
  - When `true`, also pre-create inbox folders for all repos/branches discovered in `/srv/repos/` (the “spicy mode” toggle).
  - When `false`, only repos and branches that already have an inbox root under `/srv/copyparty/inbox/` are considered.
- `branch_mode` (string, default: `"all"`)
  - Controls what branches the tree-builder advertises.
  - Supported values: `"all"`, `"whitelist"`, `"blacklist"`, `"both"`.
  - The script currently uses older `PROTECTED_BRANCHES`, but this key documents the planned switch to a configurable mode.
- `branch_whitelist` (list, default: `[]`)
  - Explicit branch names allowed when `branch_mode` is `"whitelist"` or `"both"`.
- `branch_blacklist` (list, default: `[]`)
  - Branch names excluded when `branch_mode` is `"blacklist"` or `"both"`.
- `scan_interval_seconds` (integer, default: `60`)
  - Placeholder for future daemon mode; today the tree-builder runs once but keeps this setting for a potential loop.

### `inbox`
- Default: `/srv/prompt-valet/inbox`
- Root directory watched for incoming prompt files.

### `processed`
- Default: `/srv/prompt-valet/processed`
- Destination for processed prompts and run outputs.

### `repos_root`
- Default: `/srv/prompt-valet/repos`
- Root folder containing Git clones, structured as `<repos_root>/<git_owner>/<repo_name>`.

### `watcher`
- `auto_clone_missing_repos` (bool, default: `true`)
  - When a prompt arrives under `inbox/<owner>/<repo>`, the watcher will auto-clone `/srv/repos/<owner>/<repo>` if it does not exist.
- `git_default_owner` (string, default: `"owner"`)
  - The GitHub handle used to construct clone URLs when auto-cloning.
- `git_default_host` (string, default: `"github.com"`)
  - The host portion of clone URLs.
- `git_protocol` (string, default: `"https"`)
  - Either `https` for credential-helper-driven clones or `ssh` for SSH-key based access.
- `cleanup_non_git_dirs` (bool, default: `true`)
  - If `/srv/repos/<repo>` exists but lacks `.git`, the watcher deletes it and re-clones when this flag is `true`; otherwise it leaves the directory untouched.
