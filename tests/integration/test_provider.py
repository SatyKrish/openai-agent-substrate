# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

import io
import json
from pathlib import Path

import pytest
from agents import Runner
from agents.run import RunConfig
from agents.sandbox import Manifest, SandboxAgent, SandboxRunConfig
from agents.sandbox.capabilities import Filesystem, Shell
from agents.sandbox.entries import Dir, File, GitRepo
from agents.sandbox.errors import ExecNonZeroError
from agents.sandbox.manifest import Environment
from agents.sandbox.snapshot import NoopSnapshot

from openai_agent_substrate.provider import MAX_FILE_BYTES, SubstrateSandboxClient, SubstrateSandboxOptions
from openai_agent_substrate.testing.model import ScriptedModel


def options():
    return SubstrateSandboxOptions(template="sandbox-template")


async def test_real_runner_binds_shell_and_filesystem_and_collects_artifact(client, server):
    manifest = Manifest(root=str(server.root), entries={"input.txt": File(content=b"original\n"), "output": Dir()})
    session = await client.create(manifest=manifest, options=options())
    model = ScriptedModel("cat input.txt > output/report.txt && cat output/report.txt", expected_output="original")
    agent = SandboxAgent(name="Integration test", model=model, default_manifest=manifest, capabilities=[Shell(), Filesystem()])
    try:
        async with session:
            result = await Runner.run(agent, "Create the report", run_config=RunConfig(
                sandbox=SandboxRunConfig(session=session), tracing_disabled=True,
            ))
            assert result.final_output == "Sandbox execution verified."
            assert model.calls == 2
            assert (await session.read(Path("output/report.txt"))).read() == b"original\n"
        assert [event[0] for event in server.control.events] == ["create", "resume", "suspend"]
        if server.requests:
            assert {request["actor_ref"] for request in server.requests} == {f"test-space/{session.state.actor_name}"}
    finally:
        await client.delete(session)


async def test_binary_file_roundtrip_chunking_and_literal_path(client, server):
    session = await client.create(manifest=Manifest(root=str(server.root)), options=options())
    payload = bytes(range(256)) * 900  # Requires multiple writes, includes invalid UTF-8.
    path = Path("nested/quoted ' file $(touch SHOULD_NOT_EXIST).bin")
    try:
        async with session:
            await session.write(path, io.BytesIO(payload))
            assert (await session.read(path)).read() == payload
            assert await session.read_bounded(path, max_bytes=257) == payload[:257]
            assert not (server.root / "SHOULD_NOT_EXIST").exists()
    finally:
        await client.delete(session)


async def test_resume_preserves_mutated_manifest_file_and_artifact(client, server):
    session = await client.create(manifest=Manifest(root=str(server.root), entries={"input.txt": File(content=b"initial")}), options=options())
    try:
        async with session:
            await session.write(Path("input.txt"), io.BytesIO(b"changed"))
            await session.write(Path("artifact.bin"), io.BytesIO(b"retained"))
        payload = json.loads(json.dumps(client.serialize_session_state(session.state)))
        assert "router_url" not in payload
        session = await client.resume(client.deserialize_session_state(payload))
        async with session:
            assert (await session.read(Path("input.txt"))).read() == b"changed"
            assert (await session.read(Path("artifact.bin"))).read() == b"retained"
        assert [e[0] for e in server.control.events].count("create") == 1
    finally:
        await client.delete(session)


async def test_nonzero_exit_preserves_stdout_and_stderr(client, server):
    session = await client.create(manifest=Manifest(root=str(server.root)), options=options())
    try:
        async with session:
            result = await session.exec("printf out; printf err >&2; exit 7")
            assert result.exit_code == 7
            assert result.stdout == b"out"
            assert result.stderr == b"err"
    finally:
        await client.delete(session)


async def test_sdk_workspace_editor_applies_create_update_delete(client, server):
    session = await client.create(manifest=Manifest(root=str(server.root)), options=options())
    try:
        async with session:
            await session.apply_patch({"type": "create_file", "path": "patched.txt", "diff": "+first\n"})
            await session.apply_patch({"type": "update_file", "path": "patched.txt", "diff": "@@\n-first\n+second\n"})
            assert (await session.read(Path("patched.txt"))).read() == b"second"
            await session.apply_patch({"type": "delete_file", "path": "patched.txt"})
            with pytest.raises(FileNotFoundError):
                await session.read(Path("patched.txt"))
    finally:
        await client.delete(session)


async def test_reading_directory_is_an_error(client, server):
    session = await client.create(manifest=Manifest(root=str(server.root), entries={"directory": Dir()}), options=options())
    try:
        async with session:
            with pytest.raises(ExecNonZeroError):
                await session.read(Path("directory"))
    finally:
        await client.delete(session)


async def test_remote_deadline_returns_nonzero_and_is_not_retried(client, server):
    session = await client.create(manifest=Manifest(root=str(server.root)), options=options())
    try:
        async with session:
            result = await session.exec("sleep", "0.5", shell=False, timeout=0.02)
            assert result.exit_code != 0
            if server.requests:
                commands = [r for r in server.requests if r["command"] == ["sleep", "0.5"]]
                assert len(commands) == 1
                assert commands[0]["timeout"] == "0.020000s"
    finally:
        await client.delete(session)


async def test_missing_and_out_of_scope_paths(client, server):
    session = await client.create(manifest=Manifest(root=str(server.root)), options=options())
    try:
        async with session:
            with pytest.raises(FileNotFoundError):
                await session.read(Path("missing.txt"))
            with pytest.raises(Exception, match="(escape|scope|outside|parent)"):
                await session.write(Path("../escape.txt"), io.BytesIO(b"bad"))
    finally:
        await client.delete(session)


async def test_file_limits_fail_before_upload(client, server):
    session = await client.create(manifest=Manifest(root=str(server.root)), options=options())
    try:
        async with session:
            with pytest.raises(ValueError, match="1 MiB"):
                await session.write(Path("too-large"), io.BytesIO(b"x" * (MAX_FILE_BYTES + 1)))
            assert not (server.root / "too-large").exists()
    finally:
        await client.delete(session)


async def test_resume_lost_actor_does_not_create_replacement(client, server):
    session = await client.create(manifest=Manifest(root=str(server.root)), options=options())
    async with session:
        pass
    state = client.serialize_session_state(session.state)
    await client.delete(session)
    resumed = await client.resume(client.deserialize_session_state(state))
    with pytest.raises(RuntimeError, match="no longer exists"):
        async with resumed:
            pass
    assert [e[0] for e in server.control.events].count("create") == 1


async def test_cross_atespace_resume_and_delete_are_rejected(client, server):
    session = await client.create(manifest=Manifest(root=str(server.root)), options=options())
    other = SubstrateSandboxClient(router_url=server.url, atespace="another-space", control=server.control)
    try:
        with pytest.raises(ValueError, match="atespace"):
            await other.resume(session.state)
        with pytest.raises(ValueError, match="owned"):
            await other.delete(session)
    finally:
        await client.delete(session)


@pytest.mark.parametrize("manifest", [
    Manifest(root="/"), Manifest(root="/workspace", environment=Environment(value={"SECRET": "not-forwarded"})),
    Manifest(entries={"nested": Dir(children={"repo": GitRepo(repo="example/repo", ref="main")})}),
])
async def test_unsupported_manifest_is_rejected_before_actor_creation(client, server, manifest):
    with pytest.raises((ValueError, NotImplementedError)):
        await client.create(manifest=manifest, options=options())
    assert server.control.events == []


async def test_portable_snapshots_are_explicitly_unsupported(client, server):
    with pytest.raises(NotImplementedError, match="snapshots"):
        await client.create(manifest=Manifest(root=str(server.root)), options=options(), snapshot=NoopSnapshot(id="test"))
    assert server.control.events == []


async def test_authenticated_proxy_is_owned_per_session_and_reopened_on_resume(server):
    from openai_agent_substrate.testing.server import LocalActorControl

    class Proxy:
        url = server.url
        closed = False

        async def aclose(self):
            self.closed = True

    class ProxyControl(LocalActorControl):
        proxies = []

        async def start_proxy(self, atespace, name):
            assert self.actors[f"{atespace}/{name}"]
            proxy = Proxy()
            self.proxies.append(proxy)
            return proxy

    control = ProxyControl()
    server.control = control
    client = SubstrateSandboxClient(atespace="test-space", control=control)
    session = await client.create(manifest=Manifest(root=str(server.root)), options=options())
    try:
        async with session:
            await session.write(Path("retained.txt"), io.BytesIO(b"authenticated path"))
            assert len(control.proxies) == 1
            assert not control.proxies[0].closed
        assert control.proxies[0].closed
        state = client.serialize_session_state(session.state)
        assert "127.0.0.1" not in json.dumps(state)
        session = await client.resume(client.deserialize_session_state(state))
        async with session:
            assert (await session.read(Path("retained.txt"))).read() == b"authenticated path"
        assert len(control.proxies) == 2
        assert all(p.closed for p in control.proxies)
    finally:
        await client.delete(session)
