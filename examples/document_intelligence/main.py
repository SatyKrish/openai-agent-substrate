# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Run the skill-driven Document Intelligence example from the repository root."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from contextlib import nullcontext
from pathlib import Path

from agents import Runner
from agents.run import RunConfig
from agents.sandbox import SandboxRunConfig

from openai_agent_substrate import SubstrateSandboxOptions
from openai_agent_substrate.testing.server import LocalExecutionServer

from .agent.agent import build_agent
from .agent.lib.artifacts import digests
from .agent.lib.configuration import select_model
from .agent.lib.verify import OUTPUTS, verify_artifacts
from .agent.sandbox.sandbox import build_client, build_manifest, stage_inputs

QUESTION = (
    "Use the document-analysis skill to compare ACME and BETA fiscal-2024 revenue "
    "and operating margins. Reconcile the KPI CSV, provide exact source citations, "
    "state whether EBITDA is available, and create the three verified output artifacts."
)


async def run(args, local=None):
    analysis_id = str(uuid.uuid4())
    inputs = stage_inputs()
    manifest = build_manifest(inputs, analysis_id, str(local.root) if local else "/workspace")
    client = build_client(args, local)
    session = await client.create(manifest=manifest, options=SubstrateSandboxOptions(template=args.template or "document-intelligence"))
    try:
        model = args.model
        if model == "scripted":
            # Test-only code is imported only after an explicit evaluation selection.
            from .evals.scripted import DocumentWorkflowModel
            model = DocumentWorkflowModel(sys.executable if local else "python3")
        agent = build_agent(manifest, model)
        async with session:
            boundary = await session.exec('test -z "${OPENAI_API_KEY:-}" && test ! -d /var/run/agent-config')
            if not boundary.ok():
                raise RuntimeError("The document sandbox must not contain the trusted harness API key or Secret mount")
            result = await Runner.run(agent, QUESTION, max_turns=25,
                run_config=RunConfig(sandbox=SandboxRunConfig(session=session), tracing_disabled=True))
            # Require the skill/tool workflow in live mode too, not just plausible output text.
            trace = [item for item in result.to_input_list() if isinstance(item, dict) and item.get("type") in
                     {"function_call", "function_call_output", "custom_tool_call", "custom_tool_call_output"}]
            tools_used = {item["name"] for item in trace if item.get("name")}
            loaded_skill = any(item.get("name") == "load_skill" and
                               json.loads(item["arguments"]).get("skill_name") == "document-analysis"
                               for item in trace if item.get("type") == "function_call")
            if not loaded_skill or not {"exec_command", "apply_patch"}.issubset(tools_used):
                raise RuntimeError("The run must load document-analysis and use sandbox shell and patch tools")
            artifacts = {}
            for name in OUTPUTS:
                with await session.read(Path("output") / name) as handle:
                    artifacts[name] = handle.read()
            verified = verify_artifacts(artifacts, inputs, analysis_id)
            with await session.read(Path("work/analyze.py")) as handle:
                generated_code = handle.read()
        response_ids = [response.response_id for response in result.raw_responses if response.response_id]
        state = client.serialize_session_state(session.state) if args.keep_actor else None
        actor = f"{session.state.atespace}/{session.state.actor_name}"
    finally:
        if not args.keep_actor:
            await client.delete(session)
        else:
            await session.aclose()
    destination = args.output_dir / analysis_id
    destination.mkdir(parents=True, exist_ok=False)
    for name, data in artifacts.items():
        (destination / name).write_bytes(data)
    (destination / "analysis.py").write_bytes(generated_code)
    # Only this synthetic task's tool inputs/outputs are saved, never system instructions.
    (destination / "tool-trace.json").write_text(json.dumps(trace, indent=2) + "\n")
    if state:
        (destination / "session.json").write_text(json.dumps(state, indent=2) + "\n")
    summary = dict(analysis_id=analysis_id,
        backend="local protocol simulator (host execution; no isolation)" if local else "Agent Substrate on Kubernetes",
        model=args.model, model_inference=args.model != "scripted", skill="document-analysis",
        api_response_ids=response_ids, execution_mode="scripted-eval" if args.model == "scripted" else "openai-live",
        tools_used=sorted(tools_used),
        actor=actor, actor_retained=args.keep_actor, artifact_directory=str(destination.resolve()),
        host_contract_verified=True, actor_api_key_absent=True, inputs=digests(inputs), artifacts=digests(artifacts),
        operating_margins_pct={row["company"]: row["operating_margin_pct"] for row in verified["rows"]})
    (destination / "verification.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["local", "substrate"], help="Defaults to substrate; --test defaults to local")
    parser.add_argument("--context", help="Explicit Kubernetes context")
    parser.add_argument("--kubeconfig", type=Path)
    parser.add_argument("--api-endpoint", help="Optional direct Substrate gRPC API address")
    parser.add_argument("--atespace", default="ate-demo-sandbox")
    parser.add_argument("--template", help="Python-capable ActorTemplate produced by deploy.py")
    parser.add_argument("--model", help="OpenAI model ID; defaults to OPENAI_MODEL")
    parser.add_argument("--test", action="store_true", help="Explicitly run the scripted execution evaluation without API calls")
    parser.add_argument("--keep-actor", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/examples/document_intelligence"))
    args = parser.parse_args(argv)
    args.backend = args.backend or ("local" if args.test else "substrate")
    try:
        args.model = select_model(args.model, test=args.test)
    except ValueError as error:
        parser.error(str(error))
    if args.backend == "substrate" and (not args.context or not args.template):
        parser.error("Substrate requires --context and the Python-capable --template from deploy.py")
    if args.backend == "local" and (args.model != "scripted" or args.keep_actor):
        parser.error("Local mode only supports a disposable scripted workflow; it executes on the host")
    return args


def main():
    args = parse_args()
    with LocalExecutionServer() if args.backend == "local" else nullcontext(None) as local:
        print(json.dumps(asyncio.run(run(args, local)), indent=2))


if __name__ == "__main__":
    main()
