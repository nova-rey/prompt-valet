#!/usr/bin/env python3
"""Entry point for the Prompt Valet HTTP control plane."""

from __future__ import annotations

import uvicorn

from prompt_valet.api.app import create_app
from prompt_valet.api.config import get_api_settings


def main() -> None:
    settings = get_api_settings()
    app = create_app(settings)
    uvicorn.run(app, host=settings.bind_host, port=settings.bind_port)


if __name__ == "__main__":
    raise SystemExit(main())
