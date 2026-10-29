# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

"""Own a loopback Actor proxy whose upstream authentication is handled by kubectl-ate."""

from __future__ import annotations

import asyncio
import re
from collections import deque
from contextlib import suppress


async def _stop(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 5)
        except TimeoutError:
            with suppress(ProcessLookupError):
                process.kill()
            await process.wait()


class ActorProxy:
    def __init__(self, process: asyncio.subprocess.Process, url: str, errors: deque[str]):
        self.process = process
        self.url = url
        self.errors = errors
        self._reader = asyncio.create_task(self._drain())

    @classmethod
    async def start(cls, command: list[str], *, timeout: float) -> ActorProxy:
        process = await asyncio.create_subprocess_exec(
            *command, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        assert process.stderr is not None
        errors: deque[str] = deque(maxlen=10)
        try:
            async with asyncio.timeout(timeout):
                while True:
                    line = await process.stderr.readline()
                    if not line:
                        await process.wait()
                        raise RuntimeError("Actor proxy exited before becoming ready: " + "".join(errors)[-2000:])
                    text = line.decode(errors="replace")
                    errors.append(text)
                    match = re.match(r"Proxying 127\.0\.0\.1:(\d+) -> ", text)
                    if match:
                        port = int(match.group(1))
                        if not 1 <= port <= 65535:
                            raise RuntimeError("Actor proxy returned an invalid loopback port")
                        return cls(process, f"http://127.0.0.1:{port}", errors)
        except BaseException:
            await _stop(process)
            raise

    async def _drain(self) -> None:
        assert self.process.stderr is not None
        while line := await self.process.stderr.readline():
            self.errors.append(line.decode(errors="replace"))

    async def aclose(self) -> None:
        await _stop(self.process)
        if not self._reader.done():
            self._reader.cancel()
        with suppress(asyncio.CancelledError):
            await self._reader
