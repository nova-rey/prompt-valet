from __future__ import annotations

from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import codex_watcher


def test_codex_watcher_respects_pv_root_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "prompt-valet.yaml"

    pv_root_dir = tmp_path / "prompt-valet"
    config_path.write_text(
        f'pv_root: "{pv_root_dir}"\n', encoding="utf-8"
    )

    monkeypatch.setattr(codex_watcher, "DEFAULT_CONFIG_PATH", config_path)

    created_paths: list[str] = []
    original_mkdir = Path.mkdir

    def tracked_mkdir(self, *args, **kwargs):
        created_paths.append(str(self))
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", tracked_mkdir)

    cfg, resolved_root = codex_watcher.load_config()

    assert resolved_root == pv_root_dir.resolve()
    assert cfg["pv_root"] == str(resolved_root)
    assert str(resolved_root) in created_paths
    assert not any(
        created.startswith(str(Path("/srv/prompt-valet")))
        for created in created_paths
    )
