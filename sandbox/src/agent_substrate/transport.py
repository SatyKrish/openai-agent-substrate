# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

"""The exact /process wire contract exposed by Substrate's upstream sandbox demo."""

from __future__ import annotations

import asyncio
import json
import math
import os
import ssl
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import httpx2


class ProcessTransportError(RuntimeError):
    """Execution outcome may be unknown. Never automatically retry the command."""


@dataclass(frozen=True)
class ProcessResult:
    stdout: bytes
    stderr: bytes
    exit_code: int


@dataclass(frozen=True)
class RouterTLS:
    """Mutual TLS to Substrate's router from a pod in the cluster.

    ``credential_bundle`` is the pod's private key and certificate chain from a
    ``podCertificate`` projected volume; the router admits the SPIFFE ID it names only if
    that ID is on its ingress allowlist. ``ca_bundle`` is the router's serving CA from a
    ``clusterTrustBundle`` projected volume. The kubelet rotates both files, so they are
    read again whenever they change.
    """

    credential_bundle: Path
    ca_bundle: Path


class ProcessTransport:
    def __init__(self, router_url: str, *, default_timeout: float = 30, max_response_bytes: int = 4 * 1024 * 1024,
                 tls: RouterTLS | None = None):
        url = urllib.parse.urlsplit(router_url)
        if (url.scheme not in {"http", "https"} or not url.hostname
                or url.username or url.password or url.query or url.fragment):
            raise ValueError("router_url must be an HTTP(S) URL without credentials, query, or fragment")
        if tls is not None and url.scheme != "https":
            raise ValueError("Router TLS needs an https:// router_url")
        if not math.isfinite(default_timeout) or default_timeout <= 0 or max_response_bytes <= 0:
            raise ValueError("Timeout and response limit must be positive")
        self.url = router_url.rstrip("/") + "/process"
        self._eviction_url = router_url.rstrip("/") + "/_substrate/eviction"
        self.default_timeout = default_timeout
        self.max_response_bytes = max_response_bytes
        self.tls = tls
        self._default_tls_context: ssl.SSLContext | bool = (
            ssl.create_default_context() if tls is None and url.scheme == "https" else False
        )
        self._tls_context: tuple[tuple[int, ...], ssl.SSLContext] | None = None
        if tls is not None:
            self._current_tls_context()  # Check the projected files before opening the first connection.

    def _current_tls_context(self) -> ssl.SSLContext | bool:
        if self.tls is None:
            return self._default_tls_context
        stamp = tuple(os.stat(path).st_mtime_ns for path in (self.tls.credential_bundle, self.tls.ca_bundle))
        cached = self._tls_context
        if cached is None or cached[0] != stamp:
            context = ssl.create_default_context(cafile=self.tls.ca_bundle)
            context.load_cert_chain(self.tls.credential_bundle)
            cached = self._tls_context = (stamp, context)
        return cached[1]

    async def _post(self, url: str, actor_ref: str, payload: bytes, *, timeout: float,
                    limit: int) -> tuple[int, bytes]:
        transport = httpx2.AsyncHTTPTransport(verify=self._current_tls_context(), trust_env=False, retries=0)
        # Own the connection in this coroutine so cancellation closes it before Actor suspension.
        async with asyncio.timeout(timeout), httpx2.AsyncClient(
            transport=transport, trust_env=False, follow_redirects=False, timeout=timeout,
        ) as client:
            async with client.stream("POST", url, content=payload, headers={
                "Content-Type": "application/json", "Ate-Target-Actor": actor_ref, "Accept-Encoding": "identity",
            }) as response:
                if response.status_code != 200:
                    return response.status_code, b""
                body = bytearray()
                async for chunk in response.aiter_raw():
                    if len(chunk) > limit - len(body):
                        raise ProcessTransportError("Execution response exceeded the configured byte limit")
                    body.extend(chunk)
                return response.status_code, bytes(body)

    async def execute(self, actor_ref: str, command: list[str], *, cwd: str, timeout: float | None = None) -> ProcessResult:
        seconds = self.default_timeout if timeout is None else timeout
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Execution timeout must be finite and positive")
        payload = json.dumps({
            "command": command, "cwd": cwd, "timeout": f"{seconds:.6f}s",
        }).encode()
        try:
            # Allow routing/resumption overhead in addition to the remote command deadline.
            status, raw = await self._post(self.url, actor_ref, payload, timeout=seconds + 15,
                                           limit=self.max_response_bytes)
            if status != 200:
                raise ProcessTransportError(f"Execution endpoint returned HTTP {status}; command was not retried")
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
            # Output is JSON text: invalid UTF-8 bytes were already replaced upstream.
            # Use the provider file API (base64) when byte-for-byte fidelity is required.
            return ProcessResult(result["stdout"].encode(), result["stderr"].encode(), result["exitCode"])
        except (OSError, ValueError, httpx2.HTTPError) as error:
            raise ProcessTransportError(f"Execution transport failed ({type(error).__name__}); outcome may be unknown") from None

    async def poll_eviction(self, actor_ref: str) -> int | None:
        """Read the current notice ID, zero while clear, or None for an older server."""
        try:
            status, raw = await self._post(self._eviction_url, actor_ref, b"", timeout=3, limit=128)
        except (OSError, ValueError, httpx2.HTTPError) as error:
            raise ProcessTransportError(f"Actor eviction poll failed ({type(error).__name__})") from None
        if status in {404, 405}:
            return None
        if status != 200:
            raise ProcessTransportError(f"Actor eviction poll returned HTTP {status}")
        try:
            result = json.loads(raw)
        except ValueError:
            raise ProcessTransportError("Invalid Actor eviction response") from None
        if (not isinstance(result, dict) or type(result.get("eviction_id")) is not int
                or not 0 <= result["eviction_id"] < (1 << 64)):
            raise ProcessTransportError("Invalid Actor eviction response")
        return result["eviction_id"]
