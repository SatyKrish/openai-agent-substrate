"""Loopback protocol simulator for the scripted demo and tests. NOT a sandbox.

Commands execute on the host. Never connect an untrusted model to this helper.
The upstream Go demo binary can replace the HTTP simulator for wire compatibility
tests; lifecycle calls still remain simulated in that mode.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class LocalActorControl:
    def __init__(self):
        self.actors: dict[str, bool] = {}
        self.events: list[tuple[str, str]] = []

    async def create(self, atespace: str, name: str, template: str) -> None:
        ref = f"{atespace}/{name}"
        if ref in self.actors:
            raise RuntimeError("Actor already exists")
        self.actors[ref] = False
        self.events.append(("create", ref))

    async def resume(self, atespace: str, name: str) -> None:
        ref = f"{atespace}/{name}"
        if ref not in self.actors:
            raise RuntimeError("Actor no longer exists; refusing to replace it")
        self.actors[ref] = True
        self.events.append(("resume", ref))

    async def suspend(self, atespace: str, name: str) -> None:
        ref = f"{atespace}/{name}"
        self.actors[ref] = False
        self.events.append(("suspend", ref))

    async def delete(self, atespace: str, name: str) -> None:
        ref = f"{atespace}/{name}"
        del self.actors[ref]
        self.events.append(("delete", ref))


class LocalExecutionServer:
    def __init__(self, *, upstream_binary: str | None = None):
        self.control = LocalActorControl()
        self.upstream_binary = upstream_binary
        self.requests: list[dict] = []
        self._server = None
        self._process = None
        self._thread = None

    def __enter__(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="openai-agent-substrate-")
        self.root = Path(self._temporary.name) / "workspace"
        if self.upstream_binary:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            self.url = f"http://127.0.0.1:{port}"
            self._process = subprocess.Popen(
                [self.upstream_binary], env={"PORT": str(port), "PATH": os.defpath},
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            for _ in range(100):
                try:
                    with urllib.request.urlopen(self.url, timeout=0.1):
                        pass
                except urllib.error.HTTPError:
                    break  # Upstream returns 404 for GET /; the server is ready.
                except OSError:
                    if self._process.poll() is not None:
                        raise RuntimeError("Upstream execution server exited during startup")
                    time.sleep(0.02)
            else:
                self.__exit__(None, None, None)
                raise RuntimeError("Upstream execution server did not become ready")
        else:
            owner = self

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_POST(self):
                    if self.path != "/process":
                        self.send_error(404)
                        return
                    ref = self.headers.get("ate-target-actor")
                    if not owner.control.actors.get(ref, False):
                        self.send_error(409, "Missing, unknown, or suspended target Actor")
                        return
                    length = int(self.headers.get("Content-Length", 0))
                    if not 0 < length <= 2 * 1024 * 1024:
                        self.send_error(413)
                        return
                    payload = json.loads(self.rfile.read(length))
                    owner.requests.append({"actor_ref": ref, **payload})
                    timeout = float(payload["timeout"].removesuffix("s"))
                    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                        process = subprocess.Popen(
                            payload["command"], cwd=payload.get("cwd"),
                            env={"PATH": os.defpath, "HOME": owner._temporary.name},
                            stdout=stdout, stderr=stderr, start_new_session=True,
                        )
                        error = ""
                        try:
                            process.wait(timeout=timeout)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait()
                            error = "signal: killed"
                        stdout.seek(0)
                        stderr.seek(0)
                        result = {
                            "stdout": stdout.read(2 * 1024 * 1024).decode(errors="replace"),
                            "stderr": stderr.read(2 * 1024 * 1024).decode(errors="replace"),
                            "exitCode": process.returncode,
                        }
                        if error:
                            result["error"] = error
                    body = json.dumps(result).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    try:
                        self.wfile.write(body)
                    except BrokenPipeError:
                        pass

            self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            self.url = f"http://127.0.0.1:{self._server.server_port}"
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *args):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._thread.join(timeout=5)
        if self._process:
            self._process.terminate()
            self._process.wait(timeout=5)
        self._temporary.cleanup()
