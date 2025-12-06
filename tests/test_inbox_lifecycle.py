from pathlib import Path

from scripts import codex_watcher


def test_statusified_name_prompt_to_running():
    assert (
        codex_watcher._statusified_name(
            "xyz.prompt.md", codex_watcher.STATUS_RUNNING
        )
        == "xyz.running.md"
    )
    assert (
        codex_watcher._statusified_name(
            "foo.txt", codex_watcher.STATUS_RUNNING
        )
        == "foo.running.txt"
    )


def test_claim_and_finalize_lifecycle(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    finished = tmp_path / "finished"
    inbox.mkdir()
    finished.mkdir()

    rel = Path("prompt-valet/main/xyz.prompt.md")
    src = inbox / rel
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("# test")

    running_path = codex_watcher.claim_inbox_prompt(inbox, rel)
    assert running_path.name == "xyz.running.md"
    assert running_path.exists()
    assert not src.exists()

    codex_watcher.finalize_inbox_prompt(
        inbox_root=inbox,
        finished_root=finished,
        rel=rel,
        status=codex_watcher.STATUS_DONE,
        delay_seconds=0.0,
    )

    finished_path = finished / rel.with_name("xyz.done.md")
    assert finished_path.exists()
    assert not running_path.exists()
