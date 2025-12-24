"""Simple API client helpers for the NiceGUI UI service."""

from __future__ import annotations

from dataclasses import dataclass
import httpx
from typing import Any, AsyncIterator, Dict, List, Sequence


@dataclass(frozen=True)
class HealthReport:
    reachable: bool
    version: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class UploadFilePayload:
    filename: str
    data: bytes
    content_type: str | None = None


class PromptValetAPIClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        trimmed = base_url.rstrip("/")
        if not trimmed:
            raise ValueError("API base URL must not be empty")
        self.base_url = trimmed
        self.timeout_seconds = timeout_seconds
        self._transport = transport

    def _httpx_client(self, timeout: httpx.Timeout) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {"timeout": timeout}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    async def ping(self) -> HealthReport:
        timeout = httpx.Timeout(self.timeout_seconds)
        try:
            async with self._httpx_client(timeout) as client:
                response = await client.get(f"{self.base_url}/healthz")
                response.raise_for_status()
                payload = response.json()
                version = None
                if isinstance(payload, dict):
                    version_value = payload.get("version")
                    if version_value is not None:
                        version = str(version_value)
                return HealthReport(reachable=True, version=version)
        except httpx.HTTPStatusError as exc:
            return HealthReport(
                reachable=False,
                detail=f"status={exc.response.status_code}",
            )
        except httpx.HTTPError as exc:
            return HealthReport(reachable=False, detail=str(exc))
        except ValueError as exc:
            return HealthReport(
                reachable=False,
                detail=f"invalid health payload: {exc}",
            )

    async def list_jobs(self) -> List[Dict[str, Any]]:
        timeout = httpx.Timeout(self.timeout_seconds)
        async with self._httpx_client(timeout) as client:
            response = await client.get(f"{self.base_url}/jobs")
            response.raise_for_status()
            payload = response.json()
        jobs = payload.get("jobs")
        if not isinstance(jobs, list):
            raise ValueError("invalid jobs payload")
        return jobs

    async def get_job_detail(self, job_id: str) -> Dict[str, Any]:
        timeout = httpx.Timeout(self.timeout_seconds)
        async with self._httpx_client(timeout) as client:
            response = await client.get(f"{self.base_url}/jobs/{job_id}")
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("invalid job detail payload")
        return payload

    async def list_targets(self) -> List[Dict[str, str | None]]:
        timeout = httpx.Timeout(self.timeout_seconds)
        async with self._httpx_client(timeout) as client:
            response = await client.get(f"{self.base_url}/targets")
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("invalid targets payload")
        return payload

    async def submit_job(
        self,
        repo: str,
        branch: str,
        markdown_text: str,
        filename: str | None = None,
    ) -> Dict[str, str]:
        timeout = httpx.Timeout(self.timeout_seconds)
        data = {
            "repo": repo,
            "branch": branch,
            "markdown_text": markdown_text,
        }
        if filename is not None:
            data["filename"] = filename
        async with self._httpx_client(timeout) as client:
            response = await client.post(f"{self.base_url}/jobs", json=data)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("invalid submit job payload")
        return payload

    async def upload_jobs(
        self,
        repo: str,
        branch: str,
        files: Sequence[UploadFilePayload],
    ) -> List[Dict[str, str]]:
        timeout = httpx.Timeout(self.timeout_seconds)
        multipart_files: list[tuple[str, tuple[str, bytes, str]]] = []
        for upload in files:
            content_type = upload.content_type or "text/markdown"
            multipart_files.append(
                ("files", (upload.filename, upload.data, content_type)),
            )
        async with self._httpx_client(timeout) as client:
            response = await client.post(
                f"{self.base_url}/jobs/upload",
                data={"repo": repo, "branch": branch},
                files=multipart_files,
            )
            response.raise_for_status()
            payload = response.json()
        jobs = payload.get("jobs")
        if not isinstance(jobs, list):
            raise ValueError("invalid upload response")
        return jobs

    async def abort_job(self, job_id: str) -> Dict[str, str]:
        timeout = httpx.Timeout(self.timeout_seconds)
        async with self._httpx_client(timeout) as client:
            response = await client.post(f"{self.base_url}/jobs/{job_id}/abort")
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("invalid abort payload")
        return payload

    async def tail_job_log(self, job_id: str, lines: int | None = None) -> str:
        timeout = httpx.Timeout(self.timeout_seconds)
        params: dict[str, Any] = {}
        if lines is not None:
            params["lines"] = lines
        async with self._httpx_client(timeout) as client:
            response = await client.get(
                f"{self.base_url}/jobs/{job_id}/log",
                params=params or None,
            )
            response.raise_for_status()
            return response.text

    async def stream_job_log(self, job_id: str) -> AsyncIterator[str]:
        timeout = httpx.Timeout(None)
        async with self._httpx_client(timeout) as client:
            async with client.stream(
                "GET", f"{self.base_url}/jobs/{job_id}/log/stream"
            ) as response:
                response.raise_for_status()
                async for raw_line in response.aiter_lines():
                    if not raw_line:
                        continue
                    if raw_line.startswith("data:"):
                        yield raw_line[5:].lstrip()
