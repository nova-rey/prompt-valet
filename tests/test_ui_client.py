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
