# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

"""Opt-in SDK eviction check on the dedicated local Kind sandbox worker."""

import asyncio
from contextlib import aclosing, suppress
import json
import os
from pathlib import Path
import re
from shlex import quote
from urllib.parse import urlsplit

import pytest
from agents import Runner
from agents.run import RunConfig
from agents.sandbox import Manifest, SandboxAgent, SandboxRunConfig
from agents.sandbox.capabilities import Shell

from agent_substrate import ActorCapacityError, KubectlActorControl, SubstrateSandboxClient, SubstrateSandboxOptions
from agent_substrate.eviction import ActorEvicted, eviction_watch
from testing.model import ScriptedModel

ROOT = Path(__file__).resolve().parents[3]
CONTEXT = "kind-openai-agent-substrate"
ATESPACE = "ate-demo-sandbox"
WORKER_POOL = "sandbox-workerpool"
FIXTURE = b"preserved across worker eviction\n"

pytestmark = pytest.mark.skipif(
    os.getenv("SUBSTRATE_RUN_WORKER_EVICTION_TESTS") != "1",
    reason="Set SUBSTRATE_RUN_WORKER_EVICTION_TESTS=1 to drain the dedicated Kind sandbox worker",
)


class _EvictionModel(ScriptedModel):
    async def get_response(self, *args, **kwargs):
        response = await super().get_response(*args, **kwargs)
        if self.calls == 1:
            tool_call, = response.output
            arguments = json.loads(tool_call.arguments)
            arguments["yield_time_ms"] = 60_000
            tool_call.arguments = json.dumps(arguments)
        return response


async def _kubectl(*command, body=None):
    process = await asyncio.create_subprocess_exec(
        *command, stdin=asyncio.subprocess.PIPE if body is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(30):
            stdout, stderr = await process.communicate(body)
    finally:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
            await process.wait()
    assert process.returncode == 0, f"{' '.join(command)} failed: {stderr.decode(errors='replace')}"
    return stdout


async def _ready_workers(kubectl):
    data = json.loads(await _kubectl(
        *kubectl, "-n", ATESPACE, "get", "pods", "-l", f"ate.dev/worker-pool={WORKER_POOL}", "-o", "json",
    ))
    return [
        (pod["metadata"]["name"], pod["metadata"]["uid"])
        for pod in data["items"]
        if not pod["metadata"].get("deletionTimestamp")
        and pod["status"].get("phase") == "Running"
        and any(condition["type"] == "Ready" and condition["status"] == "True"
                for condition in pod["status"].get("conditions", []))
    ]


async def _assert_idle(control, *, own_actor=None):
    data = json.loads(await _kubectl(*control.base_command(), "get", "actors", "--all-atespaces", "-o", "json"))
    for actor in data.get("actors", []):
        identity = (actor["metadata"]["atespace"], actor["metadata"]["name"])
        if identity == (ATESPACE, own_actor):
            continue
        status = actor["status"]
        assert status["state"] in {"ACTOR_STATE_SUSPENDED", "ACTOR_STATE_CRASHED"} and not status.get("workerAssignment"), (
            f"Worker drain requires an idle cluster. Actor {'/'.join(identity)} is {status['state']}."
        )


async def _actor_status(control, actor_name):
    return json.loads(await _kubectl(*control.command("get", ATESPACE, actor_name, "-o", "json")))["status"]


async def _wait_for_golden_snapshot(control, template):
    # Substrate deletes its temporary golden Actor before recording the tag.
    async with asyncio.timeout(120):
        while True:
            data = json.loads(await _kubectl(
                *control.base_command(), "get", "actor-template", template, "--atespace", ATESPACE, "-o", "json",
            ))
            golden = data.get("status", {}).get("goldenSnapshotStatus", {})
            assert not golden.get("errorMessage"), golden["errorMessage"]
            if golden.get("goldenTag"):
                return
            await asyncio.sleep(0.5)


def _assigned_worker(status):
    assert status["state"] == "ACTOR_STATE_RUNNING", status
    assignment = status["workerAssignment"]
    assert (assignment["workerNamespace"], assignment["workerPool"]) == (ATESPACE, WORKER_POOL), assignment
    return assignment["workerPod"], assignment["workerPodUid"]


async def test_worker_drain_interrupts_sdk_shell_and_restores_checkpoint(tmp_path):
    context = os.environ.get("SUBSTRATE_KUBE_CONTEXT")
    assert context == CONTEXT, f"Worker drain is restricted to {CONTEXT}"
    kubeconfig = Path(os.environ["SUBSTRATE_KUBECONFIG"]).resolve(strict=True)
    assert kubeconfig.is_file(), "Worker drain requires an explicit kubeconfig file"
    template = os.environ["SUBSTRATE_EVICTION_TEMPLATE"]
    assert re.fullmatch(r"starter-[0-9a-f]{12}", template), "Build and register the local Starter template first"

    kubectl = ["kubectl", "--context", context, "--kubeconfig", str(kubeconfig), "--request-timeout=15s"]
    server = await _kubectl(*kubectl, "config", "view", "--minify", "-o", "jsonpath={.clusters[0].cluster.server}")
    assert urlsplit(server.decode()).hostname in {"127.0.0.1", "localhost", "::1"}, "Use the local Kind API endpoint"
    namespace = json.loads(await _kubectl(*kubectl, "get", "namespace", "kube-system", "-o", "json"))
    expected_revision = (ROOT / ".substrate-revision").read_text().strip()
    assert namespace["metadata"].get("annotations", {}).get("openai-agent-substrate.dev/substrate-revision") == expected_revision, (
        "Use the dedicated Kind cluster initialized at this checkout's Substrate revision"
    )

    control = KubectlActorControl(context=context, kubeconfig=kubeconfig)
    await _wait_for_golden_snapshot(control, template)
    workers = await _ready_workers(kubectl)
    assert len(workers) == 1, f"Expected one ready {WORKER_POOL} Pod; found {workers}"
    worker = workers[0]
    await _assert_idle(control)

    client = SubstrateSandboxClient(atespace=ATESPACE, control=control, exec_timeout=90)
    manifest = Manifest(root="/workspace")
    fixture_text = quote(FIXTURE.decode().rstrip("\n"))
    model = _EvictionModel(
        f"printf '%s\\n' {fixture_text} > checkpoint.txt && printf '%s\\n' \"$$\" > shell.pid && exec sleep 90",
        expected_output="The checkpointing command should not complete normally.",
    )
    agent = SandboxAgent(name="Worker drain check", model=model, default_manifest=manifest, capabilities=[Shell()])
    session = await client.create(manifest=manifest, options=SubstrateSandboxOptions(template=template))
    actor_name = session.state.actor_name
    pending_file = tmp_path / "pending-session.json"
    drain_submitted = asyncio.Event()

    async def persist_notice():
        pending_file.write_text(json.dumps(client.serialize_session_state(session.state)))

    async def request_drain(turn):
        # The SDK shell publishes its PID just before it blocks, and exec keeps that PID.
        active_shell = 'test -s checkpoint.txt && test -s shell.pid && kill -0 "$(cat shell.pid)"'
        async with asyncio.timeout(20):
            while not (await session.exec(active_shell)).ok():
                await asyncio.sleep(0.1)
        assert await _ready_workers(kubectl) == [worker], "The dedicated worker changed before the drain"
        assert _assigned_worker(await _actor_status(control, actor_name)) == worker
        await _assert_idle(control, own_actor=actor_name)
        assert not turn.done() and (await session.exec(active_shell)).ok(), "The SDK shell completed before the drain"
        pod, uid = worker
        # Preserve the Pod's normal grace period and refuse to delete a replacement with the same name.
        options = json.dumps({"apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {"uid": uid}}).encode()
        drain_submitted.set()
        await _kubectl(*kubectl, "delete", "--raw", f"/api/v1/namespaces/{ATESPACE}/pods/{pod}", "-f", "-", body=options)

    try:
        run_config = RunConfig(sandbox=SandboxRunConfig(session=session), tracing_disabled=True)
        with pytest.raises(ActorEvicted, match="worker is shutting down"):
            async with session, eviction_watch(client, session, interval_s=0.25, persist=persist_notice):
                assert await client.poll_eviction(session) == 0, "The local Starter image must serve eviction notices"
                turn = asyncio.create_task(Runner.run(agent, "Write the checkpoint and wait.", run_config=run_config))
                drain = asyncio.create_task(request_drain(turn))
                try:
                    done, _ = await asyncio.wait((turn, drain), return_when=asyncio.FIRST_COMPLETED)
                    if drain in done:
                        drain.result()
                    await turn
                finally:
                    turn.cancel()
                    if not drain_submitted.is_set():
                        # A finished turn must never leave a task that might drain the worker later.
                        drain.cancel()
                    await asyncio.gather(turn, drain, return_exceptions=True)

        assert not drain.cancelled(), "The test did not request a worker drain"
        drain.result()
        assert model.calls == 1, "The SDK started another model step before eviction interrupted the run"
        pending = client.deserialize_session_state(json.loads(pending_file.read_text()))
        notice = pending.pending_eviction_id
        assert notice > 0 and pending.handled_eviction_id == 0
        assert session.state.pending_eviction_id == 0 and session.state.handled_eviction_id == notice

        status = await _actor_status(control, actor_name)
        assert status["state"] == "ACTOR_STATE_SUSPENDED" and not status.get("workerAssignment"), status
        assert any(
            snapshot["generation"] == status["lastAssignedGeneration"]
            and snapshot["owner"] == "SNAPSHOT_OWNER_ACTOR"
            and snapshot.get("durableSnapshot", {}).get("state") == "SNAPSHOT_STORAGE_STATE_COMPLETED"
            for snapshot in status.get("snapshots", [])
        ), f"No completed durable checkpoint for the suspended Actor: {status}"

        async with asyncio.timeout(120):
            while not (replacements := [pod for pod in await _ready_workers(kubectl) if pod[1] != worker[1]]):
                await asyncio.sleep(0.5)
        resumed = await client.resume(pending)
        async with aclosing(resumed):
            async with asyncio.timeout(60):
                while True:
                    try:
                        await resumed.start()
                        break
                    except ActorCapacityError:
                        await asyncio.sleep(0.5)
            replacement = _assigned_worker(await _actor_status(control, actor_name))
            assert replacement in replacements
            assert resumed.state.actor_name == actor_name
            assert resumed.state.pending_eviction_id == 0 and resumed.state.handled_eviction_id == notice
            with await resumed.read(Path("checkpoint.txt")) as checkpoint:
                assert checkpoint.read() == FIXTURE
            route_notice = await client.poll_eviction(resumed)
            assert route_notice in {0, notice}
            assert not client.note_eviction(resumed, route_notice)
    finally:
        await client.delete(session)

    print(f"Worker drain interrupted the SDK shell on {worker[0]}. Workspace check passed on {replacement[0]}.")
