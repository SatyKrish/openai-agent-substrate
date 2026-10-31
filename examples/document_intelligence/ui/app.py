# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""This agent's UI: the harness's shared page, configured here, plus PDF page images and agent.js's PDF view."""

from __future__ import annotations

import re
from pathlib import Path

from starlette.responses import FileResponse, JSONResponse

from openai_harness import http
from openai_harness.chat import HarnessChatServer

from ..agent.lib import pdf
from ..agent.spec import INPUTS

STATIC = Path(__file__).resolve().parent / "static"
# Page text and images of an uploaded PDF, as the server wrote them on the host.
# What the shared page shows for this agent; __main__ adds the model, cluster and prompts.
UI = {
    "title": "Document Intelligence",
    "icon": "document",
    "new_label": "New analysis",
    "greeting": "Analyze the sample reports or your own documents in an isolated workspace",
    "placeholder": "Ask about the reports, or attach PDF, .md, .txt or .csv files…",
    "starting_note": "Every new analysis starts with these sample reports, unless its first message attaches files.",
    "intro": "Each conversation gets its own Agent Substrate Actor with its own workspace volume. After every turn the "
             "server copies the workspace files here, checks the results against the original documents, and suspends "
             "the Actor until your next message.",
    "follow_ups": [
        {"label": "Which figures are computed?",
         "prompt": "Which figures in your answer are computed rather than quoted? Show each calculation."},
        {"label": "Is EBITDA disclosed?", "prompt": "Is EBITDA disclosed in either report? Cite the exact sources."},
    ],
}
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


def create_app(server: HarnessChatServer, *, config: dict, port: int, jaeger_url: str | None, capacity=None):
    return http.ui_application(server, config={**UI, **config}, port=port, jaeger_url=jaeger_url, static=STATIC,
                               starting_files={f"inputs/{name}": data for name, data in INPUTS.items()},
                               state_fields=("analysis_id", "sources"), derived_file=pdf_page, capacity=capacity)
