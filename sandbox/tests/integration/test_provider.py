# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

import io
import json
import os
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

from agent_substrate.provider import MAX_FILE_BYTES, SubstrateSandboxClient, SubstrateSandboxOptions
from agent_substrate.testing.model import ScriptedModel


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
            with pytest.raises(ValueError, match="10 MiB file limit"):
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
    from agent_substrate.testing.server import LocalActorControl

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


@pytest.mark.parametrize("symlink", [False, True])
async def test_write_refuses_directory_without_leaving_uploads(client, server, symlink):
    session = await client.create(manifest=Manifest(root=str(server.root), entries={"directory": Dir()}), options=options())
    try:
        async with session:
            target = Path("directory")
            if symlink:
                await session.exec("ln", "-s", "directory", "directory-link", shell=False)
                target = Path("directory-link")
            with pytest.raises(ExecNonZeroError):
                await session.write(target, io.BytesIO(b"must not become a child file"))
            assert (server.root / "directory").is_dir()
            assert not list(server.root.rglob(".oas-upload-*"))
    finally:
        await client.delete(session)


@pytest.mark.parametrize("upload_fails", [False, True])
async def test_upload_cleanup_never_masks_result(client, server, monkeypatch, upload_fails):
    session = await client.create(manifest=Manifest(root=str(server.root)), options=options())
    try:
        async with session:
            original = session._inner._checked

            async def failing_checked(*command):
                if command[0] == "rm":
                    raise RuntimeError("cleanup transport failure")
                if upload_fails and command[0] == "sh" and "base64 -d" in command[2]:
                    raise RuntimeError("original upload transport failure")
                return await original(*command)

            monkeypatch.setattr(session._inner, "_checked", failing_checked)
            if upload_fails:
                with pytest.raises(RuntimeError, match="original upload transport failure"):
                    await session.write(Path("result.txt"), io.BytesIO(b"data"))
            else:
                await session.write(Path("result.txt"), io.BytesIO(b"data"))
                assert (server.root / "result.txt").read_bytes() == b"data"
    finally:
        await client.delete(session)


async def test_command_output_is_utf8_text_but_file_reads_preserve_bytes(client, server):
    session = await client.create(manifest=Manifest(root=str(server.root)), options=options())
    try:
        async with session:
            result = await session.exec("printf '\\377\\376' > raw.bin; cat raw.bin")
            assert result.stdout == "\ufffd\ufffd".encode("utf-8")
            assert (await session.read(Path("raw.bin"))).read() == b"\xff\xfe"
    finally:
        await client.delete(session)


def write_commands(server):
    return [request for request in server.requests if "base64 -d" in " ".join(request["command"][:3])]


async def test_large_files_round_trip_in_few_commands(client, server):
    session = await client.create(manifest=Manifest(root=str(server.root)), options=options())
    payload = os.urandom(2 * 1024 * 1024 + 12345)  # several write commands and three read chunks
    try:
        async with session:
            before = len(server.requests)
            await session.write(Path("large/blob.bin"), io.BytesIO(payload))
            if server.requests:
                # 7 parts of 90 KiB per command: 2 MiB needs 4 commands instead of the 45 a 48 KiB chunk scheme used.
                assert len(server.requests) - before == 4
            read_before = len(server.requests)
            assert (await session.read(Path("large/blob.bin"))).read() == payload
            if server.requests:
                assert len(server.requests) - read_before == 3
            assert await session.read_bounded(Path("large/blob.bin"), max_bytes=1024 * 1024 + 7) == payload[:1024 * 1024 + 7]
            assert not list(server.root.rglob(".oas-upload-*"))
    finally:
        await client.delete(session)


async def test_small_and_empty_files_take_one_command(client, server):
    session = await client.create(manifest=Manifest(root=str(server.root)), options=options())
    try:
        async with session:
            for name, payload in (("small.txt", b"hello"), ("empty.txt", b"")):
                before = len(server.requests)
                await session.write(Path(name), io.BytesIO(payload))
                if server.requests:
                    assert len(server.requests) - before == 1
                assert (await session.read(Path(name))).read() == payload
    finally:
        await client.delete(session)


async def test_file_limit_is_configurable_and_checked_before_transfer(server):
    client = SubstrateSandboxClient(router_url=server.url, atespace="test-space", control=server.control, max_file_bytes=1000)
    session = await client.create(manifest=Manifest(root=str(server.root)), options=options())
    try:
        async with session:
            with pytest.raises(ValueError, match="file limit"):
                await session.write(Path("too-big"), io.BytesIO(b"x" * 1001))
            await session.exec("sh", "-c", "head -c 5000 /dev/zero > made-in-sandbox.bin", shell=False)
            before = len(server.requests)
            with pytest.raises(ValueError, match="file limit"):
                await session.read(Path("made-in-sandbox.bin"))
            if server.requests:
                assert len(server.requests) - before == 1  # refused from the size, without reading the rest
            assert await session.read_bounded(Path("made-in-sandbox.bin"), max_bytes=600) == b"\0" * 600
    finally:
        await client.delete(session)
    with pytest.raises(ValueError, match="positive"):
        SubstrateSandboxClient(router_url=server.url, atespace="test-space", control=server.control, max_file_bytes=0)


async def test_a_failure_between_write_commands_leaves_no_partial_file(client, server, monkeypatch):
    session = await client.create(manifest=Manifest(root=str(server.root)), options=options())
    try:
        async with session:
            original = session._inner._checked
            calls = []

            async def fail_second_write(*command):
                if command[0] == "sh" and "base64 -d" in command[2]:
                    calls.append(command)
                    if len(calls) == 2:
                        raise RuntimeError("transport lost between commands")
                return await original(*command)

            monkeypatch.setattr(session._inner, "_checked", fail_second_write)
            with pytest.raises(RuntimeError, match="transport lost"):
                await session.write(Path("partial.bin"), io.BytesIO(os.urandom(1024 * 1024)))
            assert not (server.root / "partial.bin").exists()
            assert not list(server.root.rglob(".oas-upload-*"))
    finally:
        await client.delete(session)
