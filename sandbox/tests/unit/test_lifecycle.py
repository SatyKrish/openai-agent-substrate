# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

import asyncio

import pytest

from openai_agent_substrate.lifecycle import ActorCapacityError, KubectlActorControl


async def test_upstream_cli_lifecycle_contract(monkeypatch):
    commands = []

    async def capture(argv):
        commands.append(argv)

    control = KubectlActorControl(context="explicit-test-context", endpoint="127.0.0.1:8080")
    monkeypatch.setattr(control, "_run", capture)
    await control.create("space", "actor", "template")
    await control.resume("space", "actor")
    await control.suspend("space", "actor")
    await control.delete("space", "actor")
    prefix = ["kubectl", "ate", "--context", "explicit-test-context", "--endpoint", "127.0.0.1:8080"]
    assert commands == [
        prefix + ["create", "actor", "actor", "--atespace", "space", "--template", "template"],
        prefix + ["resume", "actor", "actor", "--atespace", "space"],
        prefix + ["suspend", "actor", "actor", "--atespace", "space", "--mode", "sync"],
        prefix + ["delete", "actor", "actor", "--atespace", "space"],
    ]


@pytest.mark.parametrize(("stderr", "expected"), [
    (b"Error: rpc error: code = ResourceExhausted desc = workflow failed at step AssignWorker: no worker has room "
     b"for the actor", ActorCapacityError),
    (b"Error: actor not found", RuntimeError),
])
async def test_a_full_worker_pool_is_reported_as_a_capacity_error(monkeypatch, stderr, expected):
    class Finished:
        returncode = 1

        async def communicate(self):
            return b"", stderr

    async def spawn(*argv, **kwargs):
        return Finished()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(RuntimeError) as raised:
        await KubectlActorControl(context="test").resume("space", "actor")
    assert type(raised.value) is expected


def test_explicit_cluster_context_is_required():
    with pytest.raises(ValueError, match="context"):
        KubectlActorControl(context="")


def test_authenticated_proxy_is_the_default_transport():
    from openai_agent_substrate.provider import SubstrateSandboxClient

    client = SubstrateSandboxClient(
        atespace="test-space", control=KubectlActorControl(context="explicit-test-context"),
    )
    assert client.transport is None


async def test_proxy_cli_uses_ephemeral_loopback_port_and_explicit_kubeconfig(monkeypatch):
    from openai_agent_substrate.proxy import ActorProxy

    commands = []

    async def capture(command, *, timeout):
        commands.append((command, timeout))
        return object()

    monkeypatch.setattr(ActorProxy, "start", capture)
    control = KubectlActorControl(context="test", kubeconfig="/tmp/project-kubeconfig", timeout=42)
    await control.start_proxy("space", "actor")
    assert commands == [([
        "kubectl", "ate", "--context", "test", "--kubeconfig", "/tmp/project-kubeconfig",
        "proxy", "actor", "actor", "--atespace", "space", "0:80", "--address", "127.0.0.1",
    ], 42)]
