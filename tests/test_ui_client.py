from __future__ import annotations

import asyncio
import httpx

from prompt_valet.ui.client import PromptValetAPIClient


def test_tail_job_log_respects_lines_parameter() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/jobs/job-123/log"
        assert request.url.params["lines"] == "100"
        return httpx.Response(200, content="line1\nline2\n")

    transport = httpx.MockTransport(handler)
    client = PromptValetAPIClient("http://example/api/v1", transport=transport)

    assert asyncio.run(client.tail_job_log("job-123", lines=100)) == "line1\nline2\n"


def test_stream_job_log_yields_data_lines() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/jobs/job-abc/log/stream"
        payload = "data: entry one\n\ndata: entry two\n\n"
        return httpx.Response(
            200,
            content=payload,
            headers={"content-type": "text/event-stream"},
        )

    transport = httpx.MockTransport(handler)
    client = PromptValetAPIClient("http://example/api/v1", transport=transport)

    seen: list[str] = []

    async def _collect() -> None:
        async for line in client.stream_job_log("job-abc"):
            seen.append(line)

    asyncio.run(_collect())

    assert seen == ["entry one", "entry two"]


def test_abort_job_posts_abort_endpoint() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v1/jobs/job-xyz/abort"
        return httpx.Response(
            200,
            json={
                "job_id": "job-xyz",
                "previous_state": "running",
                "abort_requested_at": "2025-01-01T00:00:00Z",
            },
        )

    transport = httpx.MockTransport(handler)
    client = PromptValetAPIClient("http://example/api/v1", transport=transport)

    payload = asyncio.run(client.abort_job("job-xyz"))
    assert payload["job_id"] == "job-xyz"
    assert payload["previous_state"] == "running"


def test_list_jobs_uses_query_parameters() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/jobs"
        assert request.url.params["state"] == "running"
        assert request.url.params["stalled"] == "true"
        assert request.url.params["limit"] == "5"
        return httpx.Response(200, json={"jobs": []})

    transport = httpx.MockTransport(handler)
    client = PromptValetAPIClient("http://example/api/v1", transport=transport)
    asyncio.run(client.list_jobs(state="running", stalled=True, limit=5))


def test_get_status_returns_dict_payload() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/status"
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "jobs": {"counts": {"running": 0}, "total": 0, "stalled_running": 0},
                "config": {},
                "targets": {"count": 0},
                "roots": {"tree_builder_root_exists": True, "runs_root_exists": True},
            },
        )

    transport = httpx.MockTransport(handler)
    client = PromptValetAPIClient("http://example/api/v1", transport=transport)

    result = asyncio.run(client.get_status())
    assert result["status"] == "ok"
