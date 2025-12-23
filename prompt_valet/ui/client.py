"""Simple API client helpers for the NiceGUI UI service."""

from __future__ import annotations

from dataclasses import dataclass
import httpx


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
