# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""This agent's UI: the harness's shared page, configured here. Copy and edit it for your agent."""

from __future__ import annotations

from openai_harness import http
from openai_harness.chat import HarnessChatServer

# What the shared page shows for this agent; __main__ adds the model, cluster and prompts.
UI = {
    "title": "Starter agent",
    "icon": "spark",
    "new_label": "New conversation",
    "greeting": "Ask the starter agent to compute, write or inspect something in its sandbox",
    "placeholder": "Ask the agent…",
    "intro": "The agent writes files in output/ and work/ in its own sandbox. After each turn the server copies them "
             "here and checks that the outputs are well formed.",
    "follow_ups": [
        {"label": "Explain the code", "prompt": "Explain the code you wrote, step by step."},
        {"label": "Save it as JSON too", "prompt": "Also save the result as JSON in output/."},
    ],
}


def create_app(server: HarnessChatServer, *, config: dict, port: int, jaeger_url: str | None, capacity=None):
    return http.ui_application(server, config={**UI, **config}, port=port, jaeger_url=jaeger_url, capacity=capacity)
