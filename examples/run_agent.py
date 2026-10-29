# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

"""Run a live OpenAI SandboxAgent against a fresh Substrate Actor on Kubernetes."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from agents import Runner
from agents.run import RunConfig
from agents.sandbox import Manifest, SandboxAgent, SandboxRunConfig
from agents.sandbox.capabilities import Filesystem, Shell
from agents.sandbox.entries import File

from openai_agent_substrate import KubectlActorControl, SubstrateSandboxClient, SubstrateSandboxOptions


async def run(args):
    client = SubstrateSandboxClient(
        router_url=args.router_url, atespace=args.atespace,
        control=KubectlActorControl(context=args.context),
    )
    manifest = Manifest(entries={"task.txt": File(content=args.task.encode())})
    agent = SandboxAgent(
        name="Kubernetes sandbox reference implementation", model=args.model,
        instructions="Read task.txt and write the result to report.txt. Work only in the sandbox workspace.",
        default_manifest=manifest, capabilities=[Shell(), Filesystem()],
    )
    session = await client.create(
        manifest=manifest, options=SubstrateSandboxOptions(template=args.template),
    )
    try:
        async with session:
            result = await Runner.run(
                agent, "Complete the task in task.txt.", max_turns=15,
                run_config=RunConfig(sandbox=SandboxRunConfig(session=session), tracing_disabled=True),
            )
            report = (await session.read(Path("report.txt"))).read()
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(report)
            print(result.final_output)
            print(f"Report saved to {args.output}")
    finally:
        await client.delete(session)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True, help="Explicit Kubernetes context")
    parser.add_argument("--model", required=True, help="An OpenAI model ID available to your account")
    parser.add_argument("--router-url", default="http://127.0.0.1:8000")
    parser.add_argument("--atespace", default="ate-demo-sandbox")
    parser.add_argument("--template", default="sandbox-template")
    parser.add_argument("--task", default="Write a short explanation of harness separate from compute.")
    parser.add_argument("--output", type=Path, default=Path("artifacts/example-report.txt"))
    args = parser.parse_args()
    if not os.getenv("OPENAI_API_KEY"):
        parser.error("Set OPENAI_API_KEY in the trusted host environment")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
