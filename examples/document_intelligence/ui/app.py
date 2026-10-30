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
from .server import MAX_UPLOAD_BYTES, DocumentChatServer, UploadRejected

STATIC = Path(__file__).resolve().parent / "static"
_THREAD_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
# Page text and images of an uploaded PDF, as the server wrote them on the host.
_PDF_PAGE = re.compile(r"inputs/derived/([A-Za-z0-9][A-Za-z0-9._-]{0,127}\.pdf)/page-(\d{3})\.(jpg|txt)")


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
        try:
            result = await server.process(await request.body(), {})
        except UploadRejected as error:
            return JSONResponse({"error": str(error)}, status_code=400)
        if isinstance(result, StreamingResult):
            return StreamingResponse(result, media_type="text/event-stream")
        return Response(content=result.json, media_type="application/json")

    async def upload(request: Request):
        # Second phase of a ChatKit upload: the browser sends the file to the URL we issued.
        if not same_origin(request):
            return JSONResponse({"error": "cross-origin request refused"}, status_code=403)
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES + 64 * 1024:
            return JSONResponse({"error": "Uploads are limited to 10 MiB per file."}, status_code=413)
        if request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
            form = await request.form(max_files=1, max_fields=4, max_part_size=MAX_UPLOAD_BYTES + 1)
            part = form.get("file")
            if part is None or not hasattr(part, "read"):
                return JSONResponse({"error": "Multipart uploads need a 'file' field."}, status_code=400)
            data = await part.read()
        else:
            data = await request.body()
        try:
            await server.uploads.write_file(request.path_params["attachment_id"], data, {})
        except NotFoundError:
            return JSONResponse({"error": "upload not found"}, status_code=404)
        except UploadRejected as error:
            return JSONResponse({"error": str(error)}, status_code=400)
        return JSONResponse({})

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
                             "analysis_id": meta.get("analysis_id"), "sources": meta.get("sources"),
                             "uploads": meta.get("uploads", []), "turns": meta.get("turns", [])},
                            headers={"Cache-Control": "no-store"})

    async def file(request: Request):
        thread = await load(request)
        turn = find_turn(thread, request.path_params["turn"]) if thread else None
        path = request.path_params["path"]
        headers = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox", "Cache-Control": "no-store"}
        page = _PDF_PAGE.fullmatch(path)
        if page and thread is not None:
            # A page of a recorded PDF upload, served from the turn that processed it.
            upload = next((item for item in thread.metadata.get("uploads", [])
                           if item["name"] == page[1] and item.get("pdf")), None)
            if upload is None or not 1 <= int(page[2]) <= upload["pdf"]["processed"]:
                return JSONResponse({"error": "file not found"}, status_code=404)
            target = server.output_dir / thread.id / f"turn-{upload['turn']}" / path
            if not target.is_file():
                return JSONResponse({"error": "file not found"}, status_code=404)
            media = "image/jpeg" if page[3] == "jpg" else "text/plain; charset=utf-8"
            return FileResponse(target, media_type=media, headers=headers)
        # Only paths the server collected and recorded can be served.
        entry = next((item for item in (turn or {}).get("files", []) if item["path"] == path and not item.get("too_large")), None)
        if entry is None:
            return JSONResponse({"error": "file not found"}, status_code=404)
        target = server.output_dir / thread.id / f"turn-{turn['number']}" / path
        if path.endswith(".pdf"):
            # The browser never renders an uploaded PDF; the panel shows the sandbox's page images instead.
            headers["Content-Disposition"] = f'attachment; filename="{Path(path).name}"'
            return FileResponse(target, media_type="application/octet-stream", headers=headers)
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
        Route("/uploads/{attachment_id}", upload, methods=["PUT", "POST"]),
        Route("/api/config", settings),
        Route("/api/threads/{thread_id}", thread_state),
        Route("/api/threads/{thread_id}/turns/{turn:int}/files/{path:path}", file),
        Route("/api/threads/{thread_id}/turns/{turn:int}/trace", trace),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ]
    # Reject other Host headers so a rebinding DNS name cannot reach the loopback server.
    middleware = [Middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])]
    return Starlette(routes=routes, middleware=middleware)
