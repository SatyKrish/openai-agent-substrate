# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

import asyncio

import pytest

from agent_substrate.lifecycle import (
    ActorAlreadyExistsError,
    ActorCapacityError,
    ActorNotFoundError,
    ActorOperationUncertainError,
    ActorResumeInProgressError,
    KubectlActorControl,
)


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


async def test_a_token_file_replaces_the_shared_ate_client_identity(monkeypatch):
    commands = []

    async def capture(argv):
        commands.append(argv)

    control = KubectlActorControl(context="in-cluster", kubeconfig="/etc/agent/kubeconfig",
                                  endpoint="api.ate-system.svc:443", token_file="/var/run/substrate/api-token")
    monkeypatch.setattr(control, "_run", capture)
    await control.resume("space", "actor")
    assert commands == [[
        "kubectl", "ate", "--context", "in-cluster", "--kubeconfig", "/etc/agent/kubeconfig",
        "--endpoint", "api.ate-system.svc:443", "--token-file", "/var/run/substrate/api-token",
        "resume", "actor", "actor", "--atespace", "space",
    ]]
    assert control.base_command() == commands[0][:10]


@pytest.mark.parametrize(("stderr", "expected"), [
    (b"Error: rpc error: code = ResourceExhausted desc = workflow failed at step AssignWorker: no worker has room "
     b"for the actor", ActorCapacityError),
    (b"Error: rpc error: code = AlreadyExists desc = Actor already exists", ActorAlreadyExistsError),
    (b"Error: rpc error: code = NotFound desc = Actor not found", ActorNotFoundError),
    (b"Error: rpc error: code = Unavailable desc = connection closed", ActorOperationUncertainError),
    (b"Error: rpc error: code = Unknown desc = transport: rpc error: code = NotFound desc = worker",
     ActorOperationUncertainError),
    (b"Error: rpc error: code = FailedPrecondition desc = workflow failed at step MarkSuspending: "
     b"MarkSuspending prerequisite not met for Actor: test/actor (got: ACTOR_STATE_RESUMING, want: ACTOR_STATE_RUNNING)",
     ActorResumeInProgressError),
    (b"Error: actor not found", RuntimeError),
])
async def test_lifecycle_errors_use_the_top_level_grpc_status(monkeypatch, stderr, expected):
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


async def test_cli_timeout_reaps_process_and_reports_uncertain_outcome(monkeypatch):
    class TimedOut:
        returncode = None

        async def communicate(self):
            if self.returncode is None:
                await asyncio.Future()
            return b"", b""

        def kill(self):
            self.returncode = -9

    process = TimedOut()

    async def spawn(*argv, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(ActorOperationUncertainError, match="did not complete"):
        await KubectlActorControl(context="test", timeout=0.01).create("space", "actor", "template")
    assert process.returncode == -9


def test_explicit_cluster_context_is_required():
    with pytest.raises(ValueError, match="context"):
        KubectlActorControl(context="")


def test_authenticated_proxy_is_the_default_transport():
    from agent_substrate.provider import SubstrateSandboxClient

    client = SubstrateSandboxClient(
        atespace="test-space", control=KubectlActorControl(context="explicit-test-context"),
    )
    assert client.transport is None


async def test_proxy_cli_uses_ephemeral_loopback_port_and_explicit_kubeconfig(monkeypatch):
    from agent_substrate.proxy import ActorProxy

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
