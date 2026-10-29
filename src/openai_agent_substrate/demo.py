# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import asyncio
import json
import os
from contextlib import nullcontext
from pathlib import Path

from agents import Runner
from agents.run import RunConfig
from agents.sandbox import Manifest, SandboxAgent, SandboxRunConfig
from agents.sandbox.capabilities import Filesystem, Shell
from agents.sandbox.entries import Dir, File

from .lifecycle import KubectlActorControl
from .testing.server import LocalExecutionServer
from .provider import SubstrateSandboxClient, SubstrateSandboxOptions
from .testing.model import ScriptedModel

_FIRST_COMMAND = (
    "mkdir -p output && cat input.txt > output/report.txt && "
    "printf '\\nExecuted in the Actor workspace.\\n' >> output/report.txt && "
    "printf 'updated after initial staging\\n' > input.txt && cat output/report.txt"
)


async def run_demo(args, local=None):
    control = local.control if local else KubectlActorControl(context=args.context, endpoint=args.api_endpoint, kubeconfig=args.kubeconfig)
    client = SubstrateSandboxClient(
        router_url=local.url if local else args.router_url,
        atespace=args.atespace, control=control,
    )
    manifest = Manifest(
        root=str(local.root) if local else "/workspace",
        entries={"input.txt": File(content=b"Hello from OpenAI SandboxAgent.\n"), "output": Dir()},
    )
    session = await client.create(
        manifest=manifest,
        options=SubstrateSandboxOptions(template=args.template),
    )
    try:
        agent = SandboxAgent(
            name="Substrate reference implementation",
            model=ScriptedModel(_FIRST_COMMAND, expected_output="Executed in the Actor workspace.")
            if args.model == "scripted" else args.model,
            instructions=(
                "Read input.txt. Write output/report.txt containing that text and the line "
                "'Executed in the Actor workspace.'. Then replace input.txt with "
                "'updated after initial staging'. Print the report."
            ),
            default_manifest=manifest, capabilities=[Shell(), Filesystem()],
        )
        async with session:
            result = await Runner.run(
                agent, "Execute the task using the sandbox tools.", max_turns=10,
                run_config=RunConfig(sandbox=SandboxRunConfig(session=session), tracing_disabled=True),
            )
            report = (await session.read(Path("output/report.txt"))).read().decode()
            input_after = (await session.read(Path("input.txt"))).read().decode()
            if "updated after initial staging" not in input_after:
                raise RuntimeError("The first run did not update input.txt")
        # Persist provider state AFTER suspension. Conversation history is separate.
        state_payload = client.serialize_session_state(session.state)
        conversation = result.to_input_list()
        session = await client.resume(client.deserialize_session_state(state_payload))
        async with session:
            resumed_input = (await session.read(Path("input.txt"))).read().decode()
            resumed_report = (await session.read(Path("output/report.txt"))).read().decode()
            if resumed_input != input_after or resumed_report != report:
                raise RuntimeError("Resume lost workspace state or reapplied the initial manifest")
            agent.model = ScriptedModel("cat input.txt && cat output/report.txt", expected_output="updated after initial staging") \
                if args.model == "scripted" else args.model
            conversation.append({"role": "user", "content": "Print input.txt and output/report.txt to verify the resumed workspace."})
            await Runner.run(
                agent, conversation, max_turns=10,
                run_config=RunConfig(sandbox=SandboxRunConfig(session=session), tracing_disabled=True),
            )
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "report.txt").write_text(report)
        summary = {
            "backend": "local protocol simulator (no sandbox isolation)" if local else "Agent Substrate",
            "model": args.model,
            "actor": f"{session.state.atespace}/{session.state.actor_name}",
            "workspace_survived_resume": True,
            "initial_manifest_was_not_reapplied": True,
            "report": str(args.output_dir / "report.txt"),
            "actor_retained": args.keep_actor,
        }
        if args.keep_actor:
            (args.output_dir / "session.json").write_text(json.dumps(client.serialize_session_state(session.state), indent=2))
        (args.output_dir / "verification.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
    finally:
        if not args.keep_actor:
            await client.delete(session)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="OpenAI SandboxAgent + Agent Substrate reference implementation")
    parser.add_argument("--backend", choices=["local", "substrate"], default="substrate")
    parser.add_argument("--router-url", help="Optional trusted HTTP gateway; defaults to an authenticated per-Actor kubectl proxy")
    parser.add_argument("--atespace", default="ate-demo-sandbox")
    parser.add_argument("--template", default="sandbox-template")
    parser.add_argument("--context", help="Explicit Kubernetes context for kubectl ate")
    parser.add_argument("--kubeconfig", type=Path, help="Explicit kubeconfig for the selected cluster")
    parser.add_argument("--api-endpoint", help="Optional kubectl ate control-plane endpoint override")
    parser.add_argument("--model", default="scripted", help="scripted (no API calls) or an OpenAI model ID")
    parser.add_argument("--keep-actor", action="store_true", help="Suspend and save session.json instead of deleting the Actor")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts"))
    parser.add_argument("--upstream-binary", help="Local wire compatibility check using the upstream Go sandbox server")
    args = parser.parse_args(argv)
    if args.backend == "substrate" and not args.context:
        parser.error("--backend substrate requires --context")
    if args.backend == "local" and (args.model != "scripted" or args.keep_actor):
        parser.error("Local mode is only a disposable, scripted protocol test; live models require Substrate")
    if args.backend == "substrate" and args.upstream_binary:
        parser.error("--upstream-binary is only for local compatibility testing")
    if args.model != "scripted" and not os.getenv("OPENAI_API_KEY"):
        parser.error("Set OPENAI_API_KEY in the host environment for a live model")
    return args


def main():
    args = parse_args()
    local_context = LocalExecutionServer(upstream_binary=args.upstream_binary) if args.backend == "local" else nullcontext(None)
    with local_context as local:
        asyncio.run(run_demo(args, local))


if __name__ == "__main__":
    main()
