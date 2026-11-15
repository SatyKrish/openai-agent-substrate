# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

"""Monitor worker eviction inside the lifetime of an application-owned Actor session."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
import logging
from typing import TYPE_CHECKING

from .transport import ProcessTransportError

if TYPE_CHECKING:
    from agents.sandbox.session import SandboxSession

    from .provider import SubstrateSandboxClient


logger = logging.getLogger(__name__)


class ActorEvicted(RuntimeError):
    """The Actor's worker is shutting down and the turn needs to stop."""


@asynccontextmanager
async def eviction_watch(
    client: SubstrateSandboxClient, session: SandboxSession, *, interval_s: float = 1,
    persist: Callable[[], Awaitable[None]] | None = None,
) -> AsyncIterator[None]:
    """Interrupt the turn and persist its notice before the outer scope suspends the Actor."""
    owner = asyncio.current_task()
    if owner is None:
        raise RuntimeError("The eviction watcher requires an asyncio task")
    initial_cancellations = owner.cancelling()
    stop = asyncio.Event()
    evicted = False
    noticed = False

    async def monitor():
        nonlocal evicted, noticed
        while not stop.is_set():
            try:
                notice = await client.poll_eviction(session)
            except ProcessTransportError as error:
                logger.debug("Actor eviction check unavailable: %s", error)
            else:
                if notice is None:
                    return
                if notice and client.note_eviction(session, notice):
                    noticed = True
                    if not stop.is_set():
                        evicted = True
                        owner.cancel()
                    return
            try:
                await asyncio.wait_for(stop.wait(), interval_s)
            except TimeoutError:
                pass

    monitor_task = asyncio.create_task(monitor())

    async def finish() -> None:
        await monitor_task
        if noticed and persist is not None:
            await persist()

    try:
        yield
    except asyncio.CancelledError:
        if evicted and owner.uncancel() <= initial_cancellations:
            raise ActorEvicted("The sandbox worker is shutting down. Retry after the Actor suspends.") from None
        raise
    finally:
        stop.set()
        # A routed poll can wake a suspended Actor. Let any in-flight HTTP
        # request finish before the outer session scope starts suspending.
        completion = asyncio.create_task(finish())
        cancelled = False
        while not completion.done():
            try:
                await asyncio.shield(completion)
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError
        completion.result()
