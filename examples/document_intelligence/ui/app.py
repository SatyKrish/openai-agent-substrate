# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""This UI's HTTP app: the harness's chat routes, PDF page images and the page itself."""

from __future__ import annotations

import re
from pathlib import Path

from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

from harness import http
from harness.chat import HarnessChatServer

from ..agent.lib import pdf

STATIC = Path(__file__).resolve().parent / "static"
# Page text and images of an uploaded PDF, as the server wrote them on the host.
_PDF_PAGE = re.compile(r"inputs/derived/([A-Za-z0-9][A-Za-z0-9._-]{0,127}\.pdf)/page-(\d{3})\.(jpg|txt)")


def pdf_page(conversation, path: str):
    """A page of a recorded PDF upload, served from the turn that processed it."""
    page = _PDF_PAGE.fullmatch(path)
    if page is None:
        return None
    upload = next((item for item in conversation.uploads if item["name"] == page[1] and item.get("pdf")), None)
    if upload is None or not 1 <= int(page[2]) <= upload["pdf"]["processed"]:
        return JSONResponse({"error": "file not found"}, status_code=404)
    target = conversation.turn_dir(upload["turn"]) / pdf.page_file(upload["name"], int(page[2]), page[3])
    if not target.is_file():
        return JSONResponse({"error": "file not found"}, status_code=404)
    media = "image/jpeg" if page[3] == "jpg" else "text/plain; charset=utf-8"
    return FileResponse(target, media_type=media, headers=http.FILE_HEADERS)


def create_app(server: HarnessChatServer, *, config: dict, port: int, jaeger_url: str | None):
    routes = http.chat_routes(server, index=STATIC / "index.html", config=config, port=port, jaeger_url=jaeger_url,
                              state_fields=("analysis_id", "sources"), derived_file=pdf_page)
    return http.application([*routes, Mount("/static", StaticFiles(directory=STATIC), name="static")])
