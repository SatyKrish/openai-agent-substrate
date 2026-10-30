# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

import asyncio
import sys

import pytest

from agent_substrate.proxy import ActorProxy


async def test_proxy_readiness_and_owned_process_cleanup():
    proxy = await ActorProxy.start(
        [sys.executable, "-u", "-c", "import sys,time; print('Proxying 127.0.0.1:43210 -> space/actor:80', file=sys.stderr, flush=True); time.sleep(30)"],
        timeout=5,
    )
    assert proxy.url == "http://127.0.0.1:43210"
    assert proxy.process.returncode is None
    await proxy.aclose()
    assert proxy.process.returncode is not None
    await proxy.aclose()


async def test_proxy_fails_with_startup_diagnostic():
    with pytest.raises(RuntimeError, match="certificate denied"):
        await ActorProxy.start(
            [sys.executable, "-c", "import sys; print('certificate denied', file=sys.stderr); sys.exit(1)"],
            timeout=5,
        )


async def test_proxy_timeout_reaps_its_process(monkeypatch):
    original = asyncio.create_subprocess_exec
    created = []

    async def capture(*args, **kwargs):
        process = await original(*args, **kwargs)
        created.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    with pytest.raises(TimeoutError):
        await ActorProxy.start([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.03)
    assert len(created) == 1
    assert created[0].returncode is not None
