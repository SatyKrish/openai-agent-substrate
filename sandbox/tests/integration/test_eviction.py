# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

import asyncio
import signal
from unittest.mock import AsyncMock

import pytest

from agent_substrate.eviction import ActorEvicted, eviction_watch
from agent_substrate.transport import ProcessTransport


async def test_legacy_execution_server_has_no_eviction_notices(server):
    if server.upstream_binary:
        pytest.skip("The repository's Python simulator represents the legacy route")
    assert await ProcessTransport(server.url).poll_eviction("test-space/actor") is None


async def test_execution_server_survives_sigterm_and_keeps_notice_until_acknowledged(server):
    if not server.upstream_binary:
        pytest.skip("Requires the built Go execution server")
    transport = ProcessTransport(server.url)
    actor = "test-space/actor"
    initial = await transport.poll_eviction(actor)
    if initial is None:
        pytest.skip("Execution server was built without the eviction extension")
    assert initial == 0

    async def next_notice(previous):
        server._process.send_signal(signal.SIGTERM)
        async with asyncio.timeout(3):
            while True:
                current = await transport.poll_eviction(actor)
                if current is not None and current > previous:
                    return current
                await asyncio.sleep(0.01)

    first = await next_notice(0)
    assert await transport.poll_eviction(actor) == first
    second = await next_notice(first)
    await transport.acknowledge_eviction(actor, first)
    assert await transport.poll_eviction(actor) == second
    await transport.acknowledge_eviction(actor, second)

    assert server._process.poll() is None
    assert await transport.poll_eviction(actor) == 0
    result = await transport.execute(actor, ["printf", "running"], cwd=str(server.root.parent))
    assert result.stdout == b"running"


async def test_eviction_watch_interrupts_active_turn():
    client = AsyncMock()
    client.poll_eviction.side_effect = [0, 7]
    session = object()
    async with asyncio.timeout(1):
        with pytest.raises(ActorEvicted, match="worker is shutting down"):
            async with eviction_watch(client, session, interval_s=0.01):
                await asyncio.Future()
    assert client.poll_eviction.await_count == 2
    client.acknowledge_eviction.assert_awaited_once_with(session, 7)
    assert asyncio.current_task().cancelling() == 0


async def test_eviction_watch_waits_for_pending_poll_on_close():
    polling, ready_to_close, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def poll(session):
        polling.set()
        await release.wait()
        return 0

    client = AsyncMock()
    client.poll_eviction.side_effect = poll

    async def turn():
        async with eviction_watch(client, object()):
            await polling.wait()
            ready_to_close.set()

    async with asyncio.timeout(1):
        task = asyncio.create_task(turn())
        try:
            await ready_to_close.wait()
            assert not task.done()
            release.set()
            await task
        finally:
            release.set()
    assert client.poll_eviction.await_count == 1
