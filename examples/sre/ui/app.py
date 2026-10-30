# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""This UI's HTTP app: the harness's chat routes and the page itself."""

from __future__ import annotations

from pathlib import Path

from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

from openai_harness import http
from openai_harness.chat import HarnessChatServer

STATIC = Path(__file__).resolve().parent / "static"


def create_app(server: HarnessChatServer, *, config: dict, port: int, jaeger_url: str | None):
    routes = http.chat_routes(server, index=STATIC / "index.html", config=config, port=port, jaeger_url=jaeger_url)
    return http.application([*routes, Mount("/static", StaticFiles(directory=STATIC), name="static")])
