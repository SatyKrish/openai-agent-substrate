# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

"""The exact /process wire contract exposed by Substrate's upstream sandbox demo."""

from __future__ import annotations

import asyncio
import json
import math
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


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
        self.default_timeout = default_timeout
        self.max_response_bytes = max_response_bytes
        self.tls = tls
        self._tls_opener: tuple[tuple[int, ...], urllib.request.OpenerDirector] | None = None
        # Deliberately avoid inheriting HTTP_PROXY and avoid replaying POST on redirects.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _RejectRedirect())
        if tls is not None:
            self._current_opener()  # Fail at construction, not on the first command, if the files are unusable.

    def _current_opener(self) -> urllib.request.OpenerDirector:
        if self.tls is None:
            return self._opener
        stamp = tuple(os.stat(path).st_mtime_ns for path in (self.tls.credential_bundle, self.tls.ca_bundle))
        cached = self._tls_opener
        if cached is None or cached[0] != stamp:
            context = ssl.create_default_context(cafile=self.tls.ca_bundle)
            context.load_cert_chain(self.tls.credential_bundle)
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}), _RejectRedirect(), urllib.request.HTTPSHandler(context=context))
            # One assignment, so a concurrent command never pairs a new stamp with an old opener.
            cached = self._tls_opener = (stamp, opener)
        return cached[1]

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
            with self._current_opener().open(request, timeout=seconds + 15) as response:
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
            # Output is JSON text: invalid UTF-8 bytes were already replaced upstream.
            # Use the provider file API (base64) when byte-for-byte fidelity is required.
            return ProcessResult(result["stdout"].encode(), result["stderr"].encode(), result["exitCode"])
        except urllib.error.HTTPError as error:
            raise ProcessTransportError(f"Execution endpoint returned HTTP {error.code}; command was not retried") from None
        except (OSError, ValueError, urllib.error.URLError) as error:
            raise ProcessTransportError(f"Execution transport failed ({type(error).__name__}); outcome may be unknown") from None
