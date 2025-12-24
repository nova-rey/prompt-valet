from __future__ import annotations

import asyncio
from typing import List

import httpx

from prompt_valet.ui.client import PromptValetAPIClient, UploadFilePayload
from tests.fixtures import stub_api


def _make_client() -> PromptValetAPIClient:
    transport = httpx.ASGITransport(app=stub_api.create_stub_app())
    return PromptValetAPIClient("http://stub/api/v1", transport=transport)


def test_list_targets_returns_expected_entries() -> None:
    client = _make_client()
    targets = asyncio.run(client.list_targets())
    assert len(targets) == 2
    assert targets[0]["repo"] == "repo-one"
    assert targets[0]["branch"] == "main"
    assert targets[0]["owner"] == "acme"


def test_submit_job_and_list_jobs_reflect_new_entry() -> None:
    client = _make_client()
    payload = asyncio.run(
        client.submit_job(
            repo="repo-one",
            branch="main",
            markdown_text="# hello",
            filename="custom.prompt.md",
        )
    )
    assert payload["job_id"].startswith("submit-")
    assert payload["inbox_path"].endswith(".prompt.md")

    jobs = asyncio.run(client.list_jobs())
    job_ids = [job["job_id"] for job in jobs]
    assert payload["job_id"] in job_ids


def test_upload_jobs_returns_expected_job_list() -> None:
    client = _make_client()
    files = [
        UploadFilePayload(
            filename="batch-1.prompt.md", data=b"content", content_type="text/markdown"
        ),
        UploadFilePayload(
            filename="batch-2.prompt.md", data=b"other", content_type="text/markdown"
        ),
    ]
    response = asyncio.run(client.upload_jobs("repo-one", "main", files))
    assert len(response) == len(files)
    job_ids = [job["job_id"] for job in response]
    assert all(job_id.startswith("upload-") for job_id in job_ids)


def test_tail_job_log_respects_lines_parameter() -> None:
    client = _make_client()
    full_log = asyncio.run(client.tail_job_log(stub_api.STREAM_JOB_ID))
    assert full_log == stub_api.STREAM_LOG_TEXT

    last_line = asyncio.run(client.tail_job_log(stub_api.STREAM_JOB_ID, lines=1))
    assert last_line.strip() == "stream log line 2"


def test_stream_job_log_yields_expected_lines() -> None:
    client = _make_client()
    seen: List[str] = []

    async def _collect() -> None:
        async for line in client.stream_job_log(stub_api.STREAM_JOB_ID):
            seen.append(line)

    asyncio.run(_collect())
    assert seen == stub_api.STREAM_SSE_LINES


def test_abort_job_success_and_conflict() -> None:
    client = _make_client()
    success_payload = asyncio.run(client.abort_job(stub_api.STREAM_JOB_ID))
    assert success_payload["previous_state"] == "running"

    try:
        asyncio.run(client.abort_job(stub_api.COMPLETED_JOB_ID))
        assert False  # should not reach
    except httpx.HTTPStatusError as exc:
        assert exc.response.status_code == 409


def test_get_job_detail_returns_saved_job() -> None:
    client = _make_client()
    detail = asyncio.run(client.get_job_detail(stub_api.STREAM_JOB_ID))
    assert detail["job_id"] == stub_api.STREAM_JOB_ID
    assert detail["state"] == "running"
