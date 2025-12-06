from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import codex_watcher


def test_derive_repo_root_from_prompt_basic(tmp_path, monkeypatch):
    inbox_root = tmp_path / "inbox"
    repos_root = tmp_path / "repos"
    owner = "nova-rey"
    repo_name = "prompt-valet"

    prompt_path = inbox_root / owner / repo_name / "runs" / "P1" / "prompt.md"
    prompt_path.parent.mkdir(parents=True)
    prompt_path.write_text("# dummy prompt")

    config = {
        "inbox": str(inbox_root),
        "repos_root": str(repos_root),
    }

    repo_root = codex_watcher.derive_repo_root_from_prompt(config, str(prompt_path))
    assert repo_root == repos_root / owner / repo_name
