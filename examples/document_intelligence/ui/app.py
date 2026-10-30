# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""HTTP routes: the page, the ChatKit endpoint and the artifact panel's API."""

from __future__ import annotations

import re
import urllib.error
from pathlib import Path

from chatkit.server import StreamingResult
from chatkit.store import NotFoundError
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import traces
from .server import DocumentChatServer

STATIC = Path(__file__).resolve().parent / "static"
_THREAD_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def create_app(server: DocumentChatServer, *, config: dict, port: int, jaeger_url: str | None) -> Starlette:
    origins = {f"http://localhost:{port}", f"http://127.0.0.1:{port}"}

    def same_origin(request: Request) -> bool:
        # The server runs a paid model and holds cluster access: refuse cross-site requests.
        origin = request.headers.get("origin")
        site = request.headers.get("sec-fetch-site")
        return (origin is None or origin in origins) and site in (None, "same-origin", "none")

    async def index(request: Request):
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    async def chatkit(request: Request):
        if not same_origin(request):
            return JSONResponse({"error": "cross-origin request refused"}, status_code=403)
        result = await server.process(await request.body(), {})
        if isinstance(result, StreamingResult):
            return StreamingResponse(result, media_type="text/event-stream")
        return Response(content=result.json, media_type="application/json")

    async def settings(request: Request):
        return JSONResponse({**config, "jaeger_url": jaeger_url})

    async def load(request: Request):
        thread_id = request.path_params["thread_id"]
        if not _THREAD_ID.fullmatch(thread_id):
            return None
        try:
            return await server.store.load_thread(thread_id, {})
        except NotFoundError:
            return None

    def find_turn(thread, number: int):
        return next((turn for turn in thread.metadata.get("turns", []) if turn["number"] == number), None)

    async def thread_state(request: Request):
        thread = await load(request)
        if thread is None:
            return JSONResponse({"error": "thread not found"}, status_code=404)
        meta = thread.metadata
        state = "running" if thread.id in server.running else ("suspended" if meta.get("substrate") else "not created")
        return JSONResponse({"thread_id": thread.id, "title": thread.title, "actor": meta.get("actor"), "state": state,
                             "analysis_id": meta.get("analysis_id"), "turns": meta.get("turns", [])},
                            headers={"Cache-Control": "no-store"})

    async def file(request: Request):
        thread = await load(request)
        turn = find_turn(thread, request.path_params["turn"]) if thread else None
        path = request.path_params["path"]
        # Only paths the server collected and recorded can be served.
        entry = next((item for item in (turn or {}).get("files", []) if item["path"] == path and not item.get("too_large")), None)
        if entry is None:
            return JSONResponse({"error": "file not found"}, status_code=404)
        target = server.output_dir / thread.id / f"turn-{turn['number']}" / path
        headers = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox", "Cache-Control": "no-store"}
        if request.query_params.get("download"):
            headers["Content-Disposition"] = f'attachment; filename="{Path(path).name}"'
        return FileResponse(target, media_type="text/plain; charset=utf-8", headers=headers)

    async def trace(request: Request):
        thread = await load(request)
        turn = find_turn(thread, request.path_params["turn"]) if thread else None
        if turn is None or not turn.get("otel_trace_id"):
            return JSONResponse({"status": "disabled"}, status_code=404)
        if not jaeger_url:
            return JSONResponse({"status": "unavailable", "otel_trace_id": turn["otel_trace_id"]}, status_code=503)
        try:
            payload = traces.fetch_trace(jaeger_url, turn["otel_trace_id"])
        except (OSError, ValueError, urllib.error.URLError):
            return JSONResponse({"status": "unavailable", "otel_trace_id": turn["otel_trace_id"]}, status_code=503)
        if payload is None:
            return JSONResponse({"status": "pending", "otel_trace_id": turn["otel_trace_id"]}, status_code=202)
        return JSONResponse({"status": "ok", "otel_trace_id": turn["otel_trace_id"], **traces.summarize(payload)},
                            headers={"Cache-Control": "no-store"})

    routes = [
        Route("/", index),
        Route("/chatkit", chatkit, methods=["POST"]),
        Route("/api/config", settings),
        Route("/api/threads/{thread_id}", thread_state),
        Route("/api/threads/{thread_id}/turns/{turn:int}/files/{path:path}", file),
        Route("/api/threads/{thread_id}/turns/{turn:int}/trace", trace),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ]
    # Reject other Host headers so a rebinding DNS name cannot reach the loopback server.
    middleware = [Middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])]
    return Starlette(routes=routes, middleware=middleware)
