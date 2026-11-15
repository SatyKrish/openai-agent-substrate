# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

import asyncio
import signal
from unittest.mock import AsyncMock, Mock

import pytest
from agents.sandbox import Manifest

from agent_substrate.eviction import ActorEvicted, eviction_watch
from agent_substrate.lifecycle import ActorOperationUncertainError
from agent_substrate.provider import SubstrateSandboxOptions
from agent_substrate.transport import ProcessTransport


async def test_legacy_execution_server_has_no_eviction_notices(server):
    if server.upstream_binary:
        pytest.skip("The repository's Python simulator represents the legacy route")
    assert await ProcessTransport(server.url).poll_eviction("test-space/actor") is None


async def test_execution_server_survives_sigterm_and_keeps_the_notice_latched(server):
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
                if current and current != previous:
                    return current
                await asyncio.sleep(0.01)

    first = await next_notice(0)
    assert await transport.poll_eviction(actor) == first
    second = await next_notice(first)
    assert server._process.poll() is None
    assert await transport.poll_eviction(actor) == second
    result = await transport.execute(actor, ["printf", "running"], cwd=str(server.root.parent))
    assert result.stdout == b"running"


async def test_eviction_watch_interrupts_active_turn():
    client = Mock()
    client.poll_eviction = AsyncMock(side_effect=[0, 6, 7])
    client.note_eviction.side_effect = lambda session, notice: notice == 7
    persist = AsyncMock()
    session = object()
    async with asyncio.timeout(1):
        with pytest.raises(ActorEvicted, match="worker is shutting down"):
            async with eviction_watch(client, session, interval_s=0.01, persist=persist):
                await asyncio.Future()
    assert client.poll_eviction.await_count == 3
    assert [call.args for call in client.note_eviction.call_args_list] == [(session, 6), (session, 7)]
    persist.assert_awaited_once_with()
    assert asyncio.current_task().cancelling() == 0


@pytest.mark.parametrize("notice", [0, 42])
async def test_eviction_watch_waits_for_pending_poll_on_close(notice):
    polling, ready_to_close, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def poll(session):
        polling.set()
        await release.wait()
        return notice

    client = Mock()
    client.poll_eviction = AsyncMock(side_effect=poll)
    persist = AsyncMock()

    async def turn():
        async with eviction_watch(client, object(), persist=persist):
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
    assert persist.await_count == int(bool(notice))


@pytest.mark.parametrize("suspend_succeeded", [False, True])
async def test_pending_notice_is_checkpointed_before_resume(client, server, monkeypatch, suspend_succeeded):
    session = await client.create(manifest=Manifest(root=str(server.root)),
                                  options=SubstrateSandboxOptions(template="sandbox-template"))
    suspend = server.control.suspend
    attempts = 0

    async def suspend_with_lost_result(*args):
        nonlocal attempts
        attempts += 1
        if suspend_succeeded and attempts == 1:
            await suspend(*args)
        raise ActorOperationUncertainError("Suspend response lost")

    poll = AsyncMock(return_value=42)
    monkeypatch.setattr(client, "poll_eviction", poll)
    recorded = []

    async def persist():
        recorded.append(client.serialize_session_state(session.state))

    try:
        with monkeypatch.context() as scoped:
            scoped.setattr(server.control, "suspend", suspend_with_lost_result)
            with pytest.raises(ActorOperationUncertainError, match="Suspend response lost"):
                async with session, eviction_watch(client, session, persist=persist):
                    await asyncio.Future()
        assert attempts == 2
        assert recorded[0]["pending_eviction_id"] == session.state.pending_eviction_id == 42
        assert recorded[0]["handled_eviction_id"] == session.state.handled_eviction_id == 0

        session = await client.resume(client.deserialize_session_state(recorded[0]))
        async with session:
            assert [event for event, _ in server.control.events[-2:]] == ["suspend", "resume"]
            assert session.state.pending_eviction_id == 0
            assert session.state.handled_eviction_id == 42
            assert not client.note_eviction(session, 42)
            assert client.note_eviction(session, 43)
        assert session.state.handled_eviction_id == 43
    finally:
        await client.delete(session)
