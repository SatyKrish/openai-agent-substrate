# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

"""Reconnect to a retained Actor and inspect its workspace without calling a model."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from openai_agent_substrate import KubectlActorControl, SubstrateSandboxClient


async def run(args):
    client = SubstrateSandboxClient(
        router_url=args.router_url, atespace=args.atespace,
        control=KubectlActorControl(context=args.context, kubeconfig=args.kubeconfig),
    )
    payload = json.loads(args.state_file.read_text())
    session = await client.resume(client.deserialize_session_state(payload))
    try:
        async with session:
            result = await session.exec(args.command)
            print(result.stdout.decode(errors="replace"), end="")
            print(result.stderr.decode(errors="replace"), end="")
            if not result.ok():
                raise RuntimeError(f"Workspace command exited with {result.exit_code}")
        if not args.delete:
            args.state_file.write_text(json.dumps(client.serialize_session_state(session.state), indent=2) + "\n")
    finally:
        if args.delete:
            await client.delete(session)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True, help="Explicit Kubernetes context")
    parser.add_argument("--state-file", type=Path, required=True, help="Trusted session JSON from --keep-actor")
    parser.add_argument("--kubeconfig", type=Path, help="Explicit kubeconfig for the selected cluster")
    parser.add_argument("--router-url", help="Optional trusted HTTP gateway; defaults to authenticated kubectl proxy")
    parser.add_argument("--atespace", default="ate-demo-sandbox")
    parser.add_argument("--command", default="pwd && ls -la", help="Command to execute in the retained Actor")
    parser.add_argument("--delete", action="store_true", help="Permanently delete this Actor after inspection")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
