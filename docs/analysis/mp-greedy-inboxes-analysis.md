# Mini-push — greedy_inboxes analysis

## Current layout
- `scripts/rebuild_inbox_tree.py` loads `DEFAULT_CONFIG` / the user's YAML, merges `watcher` + `tree_builder`, and returns `(cfg, upstream_enabled)` where `upstream_enabled` is true only when the YAML file exists and parses.
- `tree_builder` currently exposes `eager_repos` (defaults to False) plus branch filters, and `load_config()` logs the resolved owner/host/protocol and whether upstream check succeeded.

## Repo discovery today
- `discover_repos(REPOS_ROOT)` walks one level deep under `/srv/repos`, yielding paths whose `.git` directories exist.
- `local_repos` is a mapping from `<repo>.name` → `Path`, while `repo_keys` is seeded with local keys plus any existing inbox children under `/srv/prompt-valet/inbox`.
- Inbox roots without local clones are processed via `reconcile_upstream_repo()` only when `upstream_enabled` is true; otherwise they are left untouched. Branch discovery already happens via `run_git_ls_remote` + `filter_branches_for_inbox` so we do not touch that logic.

## Greedy plan
1. Add `tree_builder.greedy_inboxes` (default False) and read it alongside existing config. When both `greedy_inboxes` and `upstream_enabled` are true, call a new helper that uses the GitHub REST API to enumerate `git_owner`'s repos (authenticated if a token env var is configured).
2. Introduce `discover_upstream_repos_for_owner(owner, host, protocol, token_env)` to build the API URL, walk pagination (`Link` header), parse JSON, and return repo names; all failures warn and return an empty list to keep the rebuild additive.
3. When greedy is enabled, expand the candidate set to `local ∪ inbox ∪ discovered`, log discovery details and how many upstream-only repos were added, and tag upstream-only repos in the processing loop so logs make the difference clear.
4. Ensure helper is easy to mock in tests, and add tests that verify greedy behavior (upstream-only inbox creation, not calling discovery when disabled or upstream is off), while keeping existing tests unchanged when greedy is false.
