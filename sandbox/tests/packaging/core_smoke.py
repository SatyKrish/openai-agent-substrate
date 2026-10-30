# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Run with the base wheel installed in a fresh environment, without extras."""

import asyncio
from importlib.metadata import PackageNotFoundError, distribution
import sys

from agents import set_tracing_disabled
from agents.sandbox import Manifest

from agent_substrate import SubstrateSandboxClient, SubstrateSandboxOptions
from agent_substrate.testing.server import LocalExecutionServer


async def main():
    for name in ("opentelemetry-sdk", "opentelemetry-exporter-otlp-proto-http"):
        try:
            distribution(name)
        except PackageNotFoundError:
            continue
        raise AssertionError(name + " should not be installed")
    assert not any(name.startswith(("opentelemetry.sdk", "opentelemetry.exporter")) for name in sys.modules)
    set_tracing_disabled(True)
    with LocalExecutionServer() as server:
        client = SubstrateSandboxClient(router_url=server.url, atespace="test-space", control=server.control)
        session = await client.create(manifest=Manifest(root=str(server.root)), options=SubstrateSandboxOptions(template="core-smoke"))
        try:
            async with session:
                result = await session.exec("printf core-only")
                assert result.stdout == b"core-only" and result.exit_code == 0
        finally:
            await client.delete(session)
    print("Core wheel import and sandbox execution passed without the OpenTelemetry SDK or OTLP exporter")


if __name__ == "__main__":
    asyncio.run(main())
