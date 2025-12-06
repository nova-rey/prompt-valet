from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import codex_watcher


def test_derive_repo_root_with_explicit_owner(tmp_path):
    inbox_root = tmp_path / "inbox"
    repos_root = tmp_path / "repos"
    owner = "nova-rey"
    repo_name = "prompt-valet"

    prompt_path = inbox_root / owner / repo_name / "main" / "P1c2-b.prompt.md"
    prompt_path.parent.mkdir(parents=True)
    prompt_path.write_text("# dummy prompt")

    config = {
        "inbox": str(inbox_root),
        "repos_root": str(repos_root),
        "git_owner": owner,
    }

    repo_root = codex_watcher.derive_repo_root_from_prompt(config, str(prompt_path))
    assert repo_root == repos_root / owner / repo_name


def test_derive_repo_root_without_owner_uses_config_owner(tmp_path):
    inbox_root = tmp_path / "inbox"
    repos_root = tmp_path / "repos"
    owner = "nova-rey"
    repo_name = "prompt-valet"

    # Old layout: repo/branch/file
    prompt_path = inbox_root / repo_name / "main" / "P1c2-b.prompt.md"
    prompt_path.parent.mkdir(parents=True)
    prompt_path.write_text("# dummy prompt")

    config = {
        "inbox": str(inbox_root),
        "repos_root": str(repos_root),
        "git_owner": owner,
    }

    repo_root = codex_watcher.derive_repo_root_from_prompt(config, str(prompt_path))
    assert repo_root == repos_root / owner / repo_name
