# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

"""A deterministic Model that drives the real SDK tool loop without an API key."""

from __future__ import annotations

import json
import uuid

from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText


class ScriptedModel(Model):
    def __init__(self, command: str, *, expected_output: str):
        self.command = command
        self.expected_output = expected_output
        self.calls = 0

    async def get_response(self, system_instructions, input, model_settings, tools,
                           output_schema, handoffs, tracing, **kwargs):
        self.calls += 1
        if self.calls == 1:
            if "exec_command" not in {tool.name for tool in tools}:
                raise AssertionError("The SDK did not bind the Shell capability")
            output = [ResponseFunctionToolCall(
                id=f"fc_{uuid.uuid4().hex}", call_id=f"call_{uuid.uuid4().hex}",
                name="exec_command", type="function_call",
                arguments=json.dumps({"cmd": self.command, "login": False, "max_output_tokens": 1000}),
            )]
        else:
            results = [item for item in input if isinstance(item, dict) and item.get("type") == "function_call_output"]
            text = json.dumps(results)
            if self.expected_output not in text or "Process exited with code 0" not in text:
                raise AssertionError(f"Sandbox tool did not return the expected successful output: {text[:1000]}")
            output = [ResponseOutputMessage(
                id=f"msg_{uuid.uuid4().hex}", role="assistant", type="message", status="completed",
                content=[ResponseOutputText(type="output_text", text="Sandbox execution verified.", annotations=[])],
            )]
        return ModelResponse(output=output, usage=Usage(), response_id=None)

    async def stream_response(self, *args, **kwargs):
        raise NotImplementedError("The key-free demo uses Runner.run(), not streaming")
        yield  # Make this an async iterator, as required by the Model interface.
