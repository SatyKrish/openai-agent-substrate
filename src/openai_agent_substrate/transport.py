# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

"""The exact /process wire contract exposed by Substrate's upstream sandbox demo."""

from __future__ import annotations

import asyncio
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


class ProcessTransportError(RuntimeError):
    """Execution outcome may be unknown. Never automatically retry the command."""


class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProcessTransportError("The execution endpoint redirected; request was not replayed")


@dataclass(frozen=True)
class ProcessResult:
    stdout: bytes
    stderr: bytes
    exit_code: int


class ProcessTransport:
    def __init__(self, router_url: str, *, default_timeout: float = 30, max_response_bytes: int = 4 * 1024 * 1024):
        url = urllib.parse.urlsplit(router_url)
        if (url.scheme not in {"http", "https"} or not url.hostname
                or url.username or url.password or url.query or url.fragment):
            raise ValueError("router_url must be an HTTP(S) URL without credentials, query, or fragment")
        if not math.isfinite(default_timeout) or default_timeout <= 0 or max_response_bytes <= 0:
            raise ValueError("Timeout and response limit must be positive")
        self.url = router_url.rstrip("/") + "/process"
        self.default_timeout = default_timeout
        self.max_response_bytes = max_response_bytes
        # Deliberately avoid inheriting HTTP_PROXY and avoid replaying POST on redirects.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _RejectRedirect())

    async def execute(self, actor_ref: str, command: list[str], *, cwd: str, timeout: float | None = None) -> ProcessResult:
        seconds = self.default_timeout if timeout is None else timeout
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Execution timeout must be finite and positive")
        payload = json.dumps({
            "command": command, "cwd": cwd, "timeout": f"{seconds:.6f}s",
        }).encode()
        return await asyncio.to_thread(self._execute, actor_ref, payload, seconds)

    def _execute(self, actor_ref: str, payload: bytes, seconds: float) -> ProcessResult:
        request = urllib.request.Request(self.url, data=payload, method="POST", headers={
            "Content-Type": "application/json", "ate-target-actor": actor_ref,
        })
        try:
            # Allow routing/resumption overhead in addition to the remote command deadline.
            with self._opener.open(request, timeout=seconds + 15) as response:
                raw = response.read(self.max_response_bytes + 1)
            if len(raw) > self.max_response_bytes:
                raise ProcessTransportError("Execution response exceeded the configured byte limit")
            result = json.loads(raw)
            if (not isinstance(result, dict) or type(result.get("exitCode")) is not int
                    or not isinstance(result.get("stdout"), str)
                    or not isinstance(result.get("stderr"), str)
                    or ("error" in result and not isinstance(result["error"], str))):
                raise ProcessTransportError("Invalid /process response schema")
            # Go's CommandContext reports timeout as an execution error, not a dedicated field.
            # Preserve the nonzero code; the upstream protocol cannot distinguish all signals.
            if result.get("error") and result["exitCode"] == 0:
                raise ProcessTransportError("Execution server reported an error with exitCode=0")
            return ProcessResult(result["stdout"].encode(), result["stderr"].encode(), result["exitCode"])
        except urllib.error.HTTPError as error:
            raise ProcessTransportError(f"Execution endpoint returned HTTP {error.code}; command was not retried") from None
        except (OSError, ValueError, urllib.error.URLError) as error:
            raise ProcessTransportError(f"Execution transport failed ({type(error).__name__}); outcome may be unknown") from None
