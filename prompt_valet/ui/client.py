"""Simple API client helpers for the NiceGUI UI service."""

from __future__ import annotations

from dataclasses import dataclass
import httpx
from typing import Any, Dict, List


@dataclass(frozen=True)
class HealthReport:
    reachable: bool
    version: str | None = None
    detail: str | None = None


class PromptValetAPIClient:
    def __init__(self, base_url: str, timeout_seconds: float = 5.0) -> None:
        trimmed = base_url.rstrip("/")
        if not trimmed:
            raise ValueError("API base URL must not be empty")
        self.base_url = trimmed
        self.timeout_seconds = timeout_seconds

    async def ping(self) -> HealthReport:
        timeout = httpx.Timeout(self.timeout_seconds)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
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
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{self.base_url}/jobs")
            response.raise_for_status()
            payload = response.json()
        jobs = payload.get("jobs")
        if not isinstance(jobs, list):
            raise ValueError("invalid jobs payload")
        return jobs

    async def get_job_detail(self, job_id: str) -> Dict[str, Any]:
        timeout = httpx.Timeout(self.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{self.base_url}/jobs/{job_id}")
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("invalid job detail payload")
        return payload
