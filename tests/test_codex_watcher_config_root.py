from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import codex_watcher


def test_load_config_respects_pv_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pv_root = tmp_path / "prompt-valet"
    config_dir = pv_root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "prompt-valet.yaml"
    config_path.write_text(
        f"""
        pv_root: "{pv_root}"
        """.strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(codex_watcher, "DEFAULT_CONFIG_PATH", config_path)

    original_mkdir = Path.mkdir
    created_paths: list[Path] = []

    def tracked_mkdir(self, *args, **kwargs):
        created_paths.append(self.expanduser().resolve())
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", tracked_mkdir)

    cfg, resolved_root = codex_watcher.load_config()

    assert resolved_root == pv_root.resolve()
    assert cfg["pv_root"] == str(resolved_root)
    assert Path("/srv/prompt-valet").resolve() not in created_paths
