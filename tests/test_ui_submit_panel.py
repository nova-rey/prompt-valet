from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, AsyncIterator, Dict, List, Sequence

from nicegui import app as nicegui_app

import prompt_valet.ui.app as ui_app_module
from prompt_valet.ui import UISettings
from prompt_valet.ui.client import HealthReport, UploadFilePayload


class ControlledPromptValetAPIClient:
    """A lightweight client stub that lets tests drive list_targets responses."""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 5.0,
        transport: Any | None = None,
    ) -> None:
        self._targets: List[Dict[str, str | None]] = []

    def set_targets(self, targets: List[Dict[str, str | None]]) -> None:
        self._targets = [dict(target) for target in targets]

    async def ping(self) -> HealthReport:
        return HealthReport(reachable=True, version="stub")

    async def list_targets(self) -> List[Dict[str, str | None]]:
        return [dict(target) for target in self._targets]

    async def list_jobs(
        self,
        state: str | None = None,
        repo: str | None = None,
        branch: str | None = None,
        stalled: bool | None = None,
        limit: int | None = None,
    ) -> List[Dict[str, Any]]:
        return []

    async def get_status(self) -> Dict[str, Any]:
        return {
            "status": "ok",
            "config": {
                "runs_root": "/tmp/runs",
                "tree_builder_root": "/tmp/tree",
                "stall_threshold_seconds": 60,
                "bind_host": "127.0.0.1",
                "bind_port": 8000,
            },
            "jobs": {"counts": {}, "total": 0, "stalled_running": 0},
            "roots": {
                "runs_root_exists": True,
                "tree_builder_root_exists": True,
            },
            "targets": {"count": len(self._targets)},
        }

    async def get_job_detail(self, job_id: str) -> Dict[str, Any]:
        return {}

    async def submit_job(
        self, repo: str, branch: str, markdown_text: str, filename: str | None = None
    ) -> Dict[str, str]:
        return {}

    async def upload_jobs(
        self, repo: str, branch: str, files: Sequence[UploadFilePayload]
    ) -> List[Dict[str, str]]:
        return []

    async def abort_job(self, job_id: str) -> Dict[str, str]:
        return {}

    async def tail_job_log(self, job_id: str, lines: int | None = None) -> str:
        return ""

    async def stream_job_log(self, job_id: str) -> AsyncIterator[str]:
        if False:
            yield ""


def test_submit_repo_selection_survives_refresh(monkeypatch) -> None:
    initial_routes = list(nicegui_app.router.routes)
    monkeypatch.setattr(
        ui_app_module, "PromptValetAPIClient", ControlledPromptValetAPIClient
    )
    test_context: Dict[str, Dict[str, Any]] = {}
    settings = UISettings(
        api_base_url="http://stub/api/v1",
        ui_bind_host="0.0.0.0",
        ui_bind_port=8080,
        api_timeout_seconds=0.1,
    )
    try:
        ui_app_module.create_ui_app(settings, test_context=test_context)
        submit_panel = test_context["submit_panel"]
        client = submit_panel["client"]
        refresh_targets = submit_panel["refresh_targets"]

        initial_targets = [
            {"repo": "repo-a", "branch": "main", "full_repo": "org/repo-a"},
            {"repo": "repo-b", "branch": "dev", "full_repo": "org/repo-b"},
        ]
        client.set_targets(initial_targets)
        asyncio.run(refresh_targets())

        submit_panel["on_repo_change"](SimpleNamespace(value="org/repo-b"))
        repo, branch = submit_panel["get_selection"]()
        assert repo == "org/repo-b"
        assert branch == "dev"

        asyncio.run(refresh_targets())
        assert submit_panel["get_selection"]()[0] == "org/repo-b"

        client.set_targets(initial_targets[:1])
        asyncio.run(refresh_targets())

        repo, branch = submit_panel["get_selection"]()
        assert repo == "org/repo-a"
        assert branch == "main"
    finally:
        nicegui_app.router.routes[:] = initial_routes
