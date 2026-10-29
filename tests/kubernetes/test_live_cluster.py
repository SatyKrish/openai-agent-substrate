# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

"""Opt-in tests that create and delete a fresh Actor on an explicitly selected local cluster."""

import io
import json
import os
from pathlib import Path

import pytest
from agents import Runner
from agents.run import RunConfig
from agents.sandbox import Manifest, SandboxAgent, SandboxRunConfig
from agents.sandbox.capabilities import Filesystem, Shell
from agents.sandbox.entries import File

from openai_agent_substrate import KubectlActorControl, SubstrateSandboxClient, SubstrateSandboxOptions
from openai_agent_substrate.testing.model import ScriptedModel

pytestmark = pytest.mark.skipif(
    os.getenv("SUBSTRATE_RUN_KUBERNETES_TESTS") != "1",
    reason="Set SUBSTRATE_RUN_KUBERNETES_TESTS=1 and an explicit local context/kubeconfig",
)


async def test_real_actor_sdk_filesystem_exit_codes_and_resume():
    context = os.environ["SUBSTRATE_KUBE_CONTEXT"]
    kubeconfig = Path(os.environ["SUBSTRATE_KUBECONFIG"])
    assert context.startswith("kind-"), "This test is restricted to an explicit local Kind context"
    assert kubeconfig.is_file()
    client = SubstrateSandboxClient(
        atespace="ate-demo-sandbox",
        control=KubectlActorControl(context=context, kubeconfig=kubeconfig),
    )
    manifest = Manifest(entries={"input.txt": File(content=b"cluster-seed")})
    session = await client.create(manifest=manifest, options=SubstrateSandboxOptions(template="sandbox-template"))
    try:
        model = ScriptedModel("cat input.txt > report.txt && printf changed > input.txt && cat report.txt", expected_output="cluster-seed")
        agent = SandboxAgent(name="Local Kubernetes verification", model=model, default_manifest=manifest, capabilities=[Shell(), Filesystem()])
        async with session:
            result = await Runner.run(agent, "Write and print the report", run_config=RunConfig(
                sandbox=SandboxRunConfig(session=session), tracing_disabled=True,
            ))
            assert result.final_output == "Sandbox execution verified."
            binary = bytes(range(256)) * 40
            await session.write(Path("binary.dat"), io.BytesIO(binary))
            assert (await session.read(Path("binary.dat"))).read() == binary
            await session.apply_patch({"type":"create_file", "path":"patch.txt", "diff":"+cluster-patch"})
            assert (await session.read(Path("patch.txt"))).read() == b"cluster-patch"
            failed = await session.exec("printf diagnostic >&2; exit 7")
            assert failed.exit_code == 7 and failed.stderr == b"diagnostic"
        saved = json.loads(json.dumps(client.serialize_session_state(session.state)))
        session = await client.resume(client.deserialize_session_state(saved))
        async with session:
            assert (await session.read(Path("input.txt"))).read() == b"changed"
            assert (await session.read(Path("report.txt"))).read() == b"cluster-seed"
            assert (await session.read(Path("binary.dat"))).read() == binary
            assert (await session.read(Path("patch.txt"))).read() == b"cluster-patch"
    finally:
        await client.delete(session)
