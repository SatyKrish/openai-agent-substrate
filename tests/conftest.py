# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

import os

import pytest
from agents import set_tracing_disabled

from openai_agent_substrate.testing.server import LocalExecutionServer
from openai_agent_substrate.provider import SubstrateSandboxClient


@pytest.fixture(autouse=True)
def disable_hosted_tracing():
    set_tracing_disabled(True)


@pytest.fixture
def server():
    with LocalExecutionServer(upstream_binary=os.getenv("SUBSTRATE_DEMO_BINARY")) as value:
        yield value


@pytest.fixture
def client(server):
    return SubstrateSandboxClient(router_url=server.url, atespace="test-space", control=server.control)
