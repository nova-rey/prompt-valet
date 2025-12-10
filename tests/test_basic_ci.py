from __future__ import annotations

from pathlib import Path
import py_compile
import re

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
CONFIGS_DIR = ROOT / "configs"

def test_scripts_exist():
    """Core Prompt Valet scripts should exist in the expected location."""
    watcher = SCRIPTS_DIR / "codex_watcher.py"
    tree_builder = SCRIPTS_DIR / "rebuild_inbox_tree.py"

    assert watcher.is_file(), f"Missing script: {watcher}"
    assert tree_builder.is_file(), f"Missing script: {tree_builder}"

def test_scripts_compile():
    """Python bytecode compilation sanity check (no syntax errors)."""
    for script in ("codex_watcher.py", "rebuild_inbox_tree.py"):
        path = SCRIPTS_DIR / script
        assert path.is_file(), f"Expected script not found: {path}"
        # Will raise PyCompileError on failure
        py_compile.compile(str(path), doraise=True)

def _load_source(path: Path) -> str:
    return path.read_text(encoding="utf-8")

def test_default_config_path_and_logging_contract():
    """
    Enforce invariants:
    - Both scripts point at /etc/prompt-valet/prompt-valet.yaml
    - Both scripts contain the standard prompt-valet log prefix.
    """
    watcher_src = _load_source(SCRIPTS_DIR / "codex_watcher.py")
    tree_src = _load_source(SCRIPTS_DIR / "rebuild_inbox_tree.py")

    # DEFAULT_CONFIG_PATH should reference the canonical YAML path in both scripts
    expected_path = "/etc/prompt-valet/prompt-valet.yaml"

    assert expected_path in watcher_src, "Watcher script is not using the canonical config path"
    assert expected_path in tree_src, "Tree-builder script is not using the canonical config path"

    # Logging contract: shared prefix and key labels
    log_pattern = re.escape("[prompt-valet] loaded config=")
    for name, src in [
        ("codex_watcher.py", watcher_src),
        ("rebuild_inbox_tree.py", tree_src),
    ]:
        assert re.search(log_pattern, src), f"{name} missing prompt-valet startup log line"
        for key in ("inbox=", "processed=", "git_owner=", "git_host=", "git_protocol=", "runner="):
            assert key in src, f"{name} startup log does not include {key}"

def test_example_config_yaml_parses_and_has_expected_sections():
    """
    configs/prompt-valet.yaml should be valid YAML and contain expected top-level keys.

    This test is intentionally shallow: we just assert that the file parses and has
    the sections our scripts expect (file_server, watcher, tree_builder, runner).
    """
    example_path = CONFIGS_DIR / "prompt-valet.yaml"
    assert example_path.is_file(), f"Missing example config: {example_path}"

    data = yaml.safe_load(example_path.read_text(encoding="utf-8"))

    assert isinstance(data, dict), "prompt-valet.yaml should parse to a mapping"

    if "file_server" in data:
        file_server = data["file_server"]
        assert isinstance(file_server, dict), "prompt-valet.yaml file_server must be a mapping"
        assert "inbox_dir" in file_server, "prompt-valet.yaml file_server missing [inbox_dir]"
        assert "processed_dir" in file_server, "prompt-valet.yaml file_server missing [processed_dir]"
    else:
        for key in ("inbox", "processed"):
            assert key in data, f"prompt-valet.yaml missing [{key}] in flat schema"

    for section in ("watcher", "tree_builder"):
        assert section in data, f"prompt-valet.yaml missing [{section}] section"

    watcher_cfg = data["watcher"]
    assert isinstance(watcher_cfg, dict), "prompt-valet.yaml watcher section must be a mapping"

    if "runner" in data:
        runner_cfg = data["runner"]
        assert isinstance(runner_cfg, dict), "prompt-valet.yaml runner section must be a mapping"
    else:
        for key in ("runner_cmd", "runner_model"):
            assert (
                key in watcher_cfg
            ), f"prompt-valet.yaml missing [{key}] entry in watcher section"
