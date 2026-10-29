# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Fixed tool decisions for repeatable execution tests; this is not inference."""

import json
import shlex
import uuid
from pathlib import Path

from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import (
    ResponseCustomToolCall, ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText,
)

SKILL_ROOT = ".agents/document-analysis"


class DocumentWorkflowModel(Model):
    def __init__(self, python="python3"):
        python = shlex.quote(python)
        helper = f"{python} {SKILL_ROOT}/scripts/documents.py"
        code = Path(__file__).with_name("scripted_analysis.py").read_text()
        patch = "*** Begin Patch\n*** Add File: work/analyze.py\n" + "\n".join("+" + line for line in code.splitlines()) + "\n*** End Patch"
        self.steps = [
            ("load_skill", {"skill_name": "document-analysis"}, "loaded"),
            ("exec_command", f"cat {SKILL_ROOT}/SKILL.md {SKILL_ROOT}/references/output-contract.md", "ROUND_HALF_UP"),
            ("exec_command", "ls inputs && cat inputs/manifest.json && grep -A 4 '## Financial performance' inputs/*.md", "ACME revenue was USD"),
            ("exec_command", f"{helper} extract", '"citations"'),
            ("apply_patch", patch, None),
            ("exec_command", f"{python} work/analyze.py && cat output/kpis.csv", "22.93"),
            ("exec_command", f"{helper} verify", "SANDBOX_DOCUMENT_ANALYSIS_VERIFIED"),
            ("exec_command", "cat output/comparison.md && cat output/result.json", "EBITDA is not disclosed"),
        ]
        self.position = 0

    async def get_response(self, system_instructions, input, model_settings, tools,
                           output_schema, handoffs, tracing, **kwargs):
        if self.position:
            results = [i for i in input if isinstance(i, dict) and i.get("type") in
                       {"function_call_output", "custom_tool_call_output"}]
            if not results:
                raise AssertionError("The SDK did not return a tool result")
            value = results[-1].get("output", "")
            last = value if isinstance(value, str) else json.dumps(value)
            previous_name, _, expected = self.steps[self.position - 1]
            if expected and expected not in last:
                raise AssertionError(f"{previous_name} did not return {expected!r}: {last[:2000]}")
            if previous_name == "exec_command" and "Process exited with code 0" not in last:
                raise AssertionError(f"Sandbox command failed: {last[:2000]}")
        if self.position == len(self.steps):
            output = [ResponseOutputMessage(
                id=f"msg_{uuid.uuid4().hex}", role="assistant", type="message", status="completed",
                content=[ResponseOutputText(type="output_text", annotations=[],
                    text="Scripted document workflow finished. Inspect the verified artifacts.")],
            )]
        else:
            name, value, _ = self.steps[self.position]
            if name not in {tool.name for tool in tools}:
                raise AssertionError(f"Required SDK tool is unavailable: {name}")
            common = dict(id=f"tool_{uuid.uuid4().hex}", call_id=f"call_{uuid.uuid4().hex}", name=name)
            if name == "apply_patch":
                output = [ResponseCustomToolCall(**common, type="custom_tool_call", input=value)]
            else:
                args = {"cmd": value, "login": False, "max_output_tokens": 4000} if name == "exec_command" else value
                output = [ResponseFunctionToolCall(**common, type="function_call", arguments=json.dumps(args))]
            self.position += 1
        return ModelResponse(output=output, usage=Usage(), response_id=None)

    async def stream_response(self, *args, **kwargs):
        raise NotImplementedError("Use Runner.run() for the deterministic workflow")
        yield
