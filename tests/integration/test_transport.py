# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from openai_agent_substrate.transport import ProcessTransport, ProcessTransportError


@contextmanager
def response_server(status, body, headers=None):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            requests.append({"actor": self.headers.get("ate-target-actor"), "body": json.loads(self.rfile.read(int(self.headers["Content-Length"])))})
            self.send_response(status)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", requests
        finally:
            server.shutdown()
            thread.join(timeout=5)


@pytest.mark.parametrize("status,body,headers", [
    (503, b"unavailable", None),
    (200, b"not-json", None),
    (200, b'{"stdout":"", "stderr":""}', None),
    (200, b'{"stdout":"", "stderr":"", "exitCode":true}', None),
    (200, b'{"stdout":"", "stderr":"", "exitCode":0, "error":"failed"}', None),
    (307, b"", {"Location": "http://127.0.0.1:1/process"}),
])
async def test_failed_execution_is_never_replayed(status, body, headers):
    with response_server(status, body, headers) as (url, requests):
        with pytest.raises(ProcessTransportError):
            await ProcessTransport(url).execute("space/actor", ["true"], cwd="/workspace")
        assert len(requests) == 1
        assert requests[0]["actor"] == "space/actor"


async def test_oversized_response_is_rejected():
    with response_server(200, b"x" * 101) as (url, requests):
        with pytest.raises(ProcessTransportError, match="byte limit"):
            await ProcessTransport(url, max_response_bytes=100).execute("space/actor", ["true"], cwd="/workspace")


@pytest.mark.parametrize("url", ["ftp://example.com", "http://user:secret@example.com", "http://example.com?token=secret"])
def test_router_does_not_accept_credentials_or_non_http_url(url):
    with pytest.raises(ValueError):
        ProcessTransport(url)
