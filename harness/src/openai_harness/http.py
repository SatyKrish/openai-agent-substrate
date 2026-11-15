# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""The HTTP side of an agent UI: the shared page, the ChatKit endpoint, uploads and the panel's API.

Every agent's UI is the same page (``static/`` here), configured by the agent's ``ui/``
folder: its title, prompts, labels and an optional ``agent.js`` that renders its own
file types. The server runs a paid model and holds cluster access, so it serves
loopback only, refuses cross-site requests, and serves only files it recorded itself.
"""

from __future__ import annotations

import asyncio
from contextlib import aclosing
import html
import logging
import re
import urllib.error
from pathlib import Path
from typing import Awaitable, Callable

from chatkit.server import StreamingResult
from chatkit.store import NotFoundError
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send

from . import traces
from .chat import HarnessChatServer, error_frame
from .spec import MIB
from .store import ThreadBusyError
from .uploads import UploadRejected

logger = logging.getLogger(__name__)
SHELL = Path(__file__).resolve().parent / "static"
_THREAD_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
FILE_HEADERS = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox", "Cache-Control": "no-store"}
_STREAM_QUEUE_EVENTS = 128
_STREAM_QUEUE_BYTES = 4 * MIB


class _ChatKitResponse(StreamingResponse):
    """Run turn cleanup in the task consuming ChatKit, independently of ASGI writes."""

    def __init__(self, result: StreamingResult):
        self._source = result.json_events
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=_STREAM_QUEUE_EVENTS + 1)
        self._buffered_bytes = 0
        self._overflowed = False
        super().__init__(self._events(), media_type="text/event-stream")

    async def _publish(self) -> None:
        async with aclosing(self._source):
            async for event in self._source:
                if self._overflowed:
                    continue
                if self._queue.qsize() >= _STREAM_QUEUE_EVENTS or self._buffered_bytes + len(event) > _STREAM_QUEUE_BYTES:
                    self._overflowed = True
                    # This task never waits outside ChatKit on a full queue. Cancellation is
                    # delivered inside the source the next time it awaits.
                    self._producer.cancel()
                    continue
                self._buffered_bytes += len(event)
                self._queue.put_nowait(event)

    async def _events(self):
        while (event := await self._queue.get()) is not None:
            self._buffered_bytes -= len(event)
            yield event
        if self._overflowed:
            yield error_frame("The response stream fell behind. Reload the conversation to see its saved state.", retry=False)
        else:
            self._producer.result()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self._producer = asyncio.create_task(self._publish())
        self._producer.add_done_callback(lambda _: self._queue.put_nowait(None))
        try:
            await super().__call__(scope, receive, send)
        finally:
            if not self._producer.done() and not self._producer.cancelling():
                self._producer.cancel()
            # Join cleanup even if the HTTP task receives another cancellation.
            cleanup = asyncio.gather(self._producer, return_exceptions=True)
            interrupted = False
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    interrupted = True
            if interrupted:
                raise asyncio.CancelledError


class _Revalidated(StaticFiles):
    """Static files the browser revalidates on every load, so an edited page is never served stale."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def chat_routes(server: HarnessChatServer, *, config: dict, port: int, jaeger_url: str | None, index: Path = SHELL / "index.html",
                state_fields: tuple[str, ...] = (), derived_file: Callable | None = None,
                starting_files: dict[str, bytes] | None = None,
                capacity: Callable[[], Awaitable[dict]] | None = None) -> list[Route]:
    """Routes for one agent's UI. ``derived_file(thread, path)`` may serve files the agent derived from uploads;
    ``starting_files`` are the workspace's initial files, shown before the first message."""
    origins = {f"http://localhost:{port}", f"http://127.0.0.1:{port}"}
    max_upload = server.spec.uploads.max_bytes if server.spec.uploads else 0

    def same_origin(request: Request) -> bool:
        origin = request.headers.get("origin")
        site = request.headers.get("sec-fetch-site")
        return (origin is None or origin in origins) and site in (None, "same-origin", "none")

    async def page(request: Request):
        title = html.escape(str(config.get("title") or "Agent"))
        return Response(index.read_text(encoding="utf-8").replace("{{title}}", title), media_type="text/html",
                        headers={"Cache-Control": "no-store"})

    async def icon(request: Request):
        return FileResponse(SHELL / "favicon.svg", media_type="image/svg+xml")

    async def chatkit(request: Request):
        if not same_origin(request):
            return JSONResponse({"error": "cross-origin request refused"}, status_code=403)
        try:
            result = await server.process(await request.body(), {})
        except ThreadBusyError as error:
            return JSONResponse({"error": str(error)}, status_code=409)
        except UploadRejected as error:
            return JSONResponse({"error": str(error)}, status_code=400)
        if isinstance(result, StreamingResult):
            return _ChatKitResponse(result)
        return Response(content=result.json, media_type="application/json")

    async def upload(request: Request):
        # Second phase of a ChatKit upload: the browser sends the file to the URL we issued.
        if not same_origin(request):
            return JSONResponse({"error": "cross-origin request refused"}, status_code=403)
        if not max_upload:
            return JSONResponse({"error": "This agent does not accept uploads."}, status_code=404)
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > max_upload + 64 * 1024:
            return JSONResponse({"error": f"Uploads are limited to {max_upload // MIB} MiB per file."}, status_code=413)
        if request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
            form = await request.form(max_files=1, max_fields=4, max_part_size=max_upload + 1)
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
        state = ("running" if thread.id in server.running
                 else "elsewhere" if server.store.thread_claimed(thread.id)
                 else "suspended" if meta.get("substrate") and not meta.get("pending_actor_template") else "not created")
        return JSONResponse({"thread_id": thread.id, "title": thread.title, "actor": meta.get("actor"), "state": state,
                             **{field: meta.get(field) for field in state_fields},
                             "uploads": meta.get("uploads", []), "turns": meta.get("turns", [])},
                            headers={"Cache-Control": "no-store"})

    async def file(request: Request):
        thread = await load(request)
        path = request.path_params["path"]
        if thread is not None and derived_file is not None:
            served = derived_file(server.conversation(thread), path)
            if served is not None:
                return served
        turn = find_turn(thread, request.path_params["turn"]) if thread else None
        # Only paths the server collected and recorded can be served.
        entry = next((item for item in (turn or {}).get("files", []) if item["path"] == path and not item.get("too_large")), None)
        if entry is None:
            return JSONResponse({"error": "file not found"}, status_code=404)
        target = server.conversation(thread).turn_dir(turn["number"]) / path
        headers = dict(FILE_HEADERS)
        if path.endswith(".pdf"):
            # The browser never renders an uploaded PDF; a UI shows the sandbox's page images instead.
            headers["Content-Disposition"] = f'attachment; filename="{Path(path).name}"'
            return FileResponse(target, media_type="application/octet-stream", headers=headers)
        if request.query_params.get("download"):
            headers["Content-Disposition"] = f'attachment; filename="{Path(path).name}"'
        return FileResponse(target, media_type="text/plain; charset=utf-8", headers=headers)

    async def starting_file(request: Request):
        path = request.path_params["path"]
        if path not in (starting_files or {}):
            return JSONResponse({"error": "file not found"}, status_code=404)
        headers = dict(FILE_HEADERS)
        if request.query_params.get("download"):
            headers["Content-Disposition"] = f'attachment; filename="{Path(path).name}"'
        return Response(starting_files[path], media_type="text/plain; charset=utf-8", headers=headers)

    async def free_slots(request: Request):
        if capacity is None:
            return JSONResponse({"status": "disabled"}, status_code=404)
        try:
            return JSONResponse({"status": "ok", **await capacity()}, headers={"Cache-Control": "no-store"})
        except Exception as error:  # the cluster may be busy or unreachable; the UI hides the count
            logger.debug("capacity unavailable: %s", error)
            return JSONResponse({"status": "unavailable"}, status_code=503)

    async def trace(request: Request):
        thread = await load(request)
        turn = find_turn(thread, request.path_params["turn"]) if thread else None
        if turn is None or not turn.get("otel_trace_id"):
            return JSONResponse({"status": "disabled"}, status_code=404)
        if not jaeger_url:
            return JSONResponse({"status": "unavailable", "otel_trace_id": turn["otel_trace_id"]}, status_code=503)
        try:
            payload = await asyncio.to_thread(traces.fetch_trace, jaeger_url, turn["otel_trace_id"])
        except (OSError, ValueError, urllib.error.URLError):
            return JSONResponse({"status": "unavailable", "otel_trace_id": turn["otel_trace_id"]}, status_code=503)
        if payload is None:
            return JSONResponse({"status": "pending", "otel_trace_id": turn["otel_trace_id"]}, status_code=202)
        return JSONResponse({"status": "ok", "otel_trace_id": turn["otel_trace_id"], **traces.summarize(payload)},
                            headers={"Cache-Control": "no-store"})

    return [
        Route("/", page),
        Route("/favicon.svg", icon),
        Route("/favicon.ico", icon),
        Route("/api/starting-files/{path:path}", starting_file),
        Route("/api/capacity", free_slots),
        Route("/chatkit", chatkit, methods=["POST"]),
        Route("/uploads/{attachment_id}", upload, methods=["PUT", "POST"]),
        Route("/api/config", settings),
        Route("/api/threads/{thread_id}", thread_state),
        Route("/api/threads/{thread_id}/turns/{turn:int}/files/{path:path}", file),
        Route("/api/threads/{thread_id}/turns/{turn:int}/trace", trace),
    ]


def ui_application(server: HarnessChatServer, *, config: dict, port: int, jaeger_url: str | None,
                   static: Path | None = None, starting_files: dict[str, bytes] | None = None, **routes) -> Starlette:
    """An agent's UI: the shared page configured by ``config``, plus the agent's own ``static`` folder.

    ``static/agent.js`` (an ES module) and ``static/agent.css`` load into the page when present.
    """
    uploads = server.spec.uploads
    full = {"name": server.spec.name, "title": server.spec.name, **config,
            "uploads": {"types": uploads.types, "hint": uploads.hint, "max_bytes": uploads.max_bytes,
                        "max_count": uploads.max_count} if uploads else None,
            "starting_files": [{"path": path, "bytes": len(data)} for path, data in (starting_files or {}).items()],
            "capacity": routes.get("capacity") is not None, "preferred_output": server.preferred_output}
    mounts = [Mount("/harness", _Revalidated(directory=SHELL), name="harness")]
    if static is not None:
        mounts.append(Mount("/static", _Revalidated(directory=static), name="static"))
        if (static / "agent.js").is_file():
            full["extension"] = "/static/agent.js"
        if (static / "agent.css").is_file():
            full["stylesheet"] = "/static/agent.css"
    return application([*chat_routes(server, config=full, port=port, jaeger_url=jaeger_url,
                                      starting_files=starting_files, **routes), *mounts])


def application(routes: list) -> Starlette:
    # Reject other Host headers so a rebinding DNS name cannot reach the loopback server.
    middleware = [Middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])]
    return Starlette(routes=routes, middleware=middleware)
