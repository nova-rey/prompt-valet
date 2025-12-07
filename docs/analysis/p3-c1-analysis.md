# Phase 3 · Checkpoint 1 Analysis

## Findings
- `scripts/rebuild_inbox_tree.py` currently builds `local_repos` by inspecting `/srv/repos` (see `discover_repos`, `main()` around lines 430-490) and then treats `inbox/<repo>` roots as valid only if there is a clone under `/srv/repos` or the upstream host responds. If the repo is missing upstream, `reconcile_upstream_repo` calls `remove_inbox_root`, which wipes the folder and writes an `ERROR.md` marker describing `/srv/repos` as the canonical source.
- That hard dependency on `/srv/repos` means a newly added repo key (for example `nova-process`) is deleted as soon as the tree builder runs if the repo has not yet been cloned locally, which violates the stated requirement of only checking syntax and letting the runner clone the repo.
- Some existing tests (`tests/test_rebuild_inbox_tree_upstream.py::test_upstream_repo_missing`) encode this behavior by asserting that an inbox folder disappears when git reports the repo is absent upstream; those assertions will need to be flipped so the tree builder retains the folder, only marks it invalid, and never peeks under `/srv/repos/` for validation.

## Implementation plan
1. Introduce a regex-backed helper (`REPO_KEY_PATTERN`, `is_valid_repo_key()`) near the top of `scripts/rebuild_inbox_tree.py`, use it whenever we encounter a repo key, and add helpers to mark a folder invalid (write `ERROR.md`) or restore it to a valid state without deleting the directory.
2. Update the main reconciliation flow to skip deletion: before processing each `repo_key`, check syntax; if it is bad, write the invalid marker and continue without running any git probes. Never call `remove_inbox_root` — instead, when an upstream check reports a missing repo, simply mark the inbox root invalid (keeping the tree intact) and rely on the runner to clone it later.
3. Update the tests so that they no longer assume `/srv/repos` is authoritative. Remove or rewrite the upstream-missing test to assert the folder stays present with an error marker, and add new tests that cover (a) a valid repo key being accepted, (b) invalid repo syntax producing an `ERROR.md` without deletion, and (c) the tree builder no longer depending on clones under `/srv/repos` to treat a repo as valid.
4. Once the new behavior stabilizes, rerun `pytest -q`, then `black` and `ruff` across the repository to keep formatting and linting clean.

## Verification notes
- `pytest -q` will gate correctness around the new markers and retention behavior.
- Running `black .` and `ruff .` should keep the script in sync with repository standards.
