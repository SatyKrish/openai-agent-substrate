# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""This agent's UI: the harness's shared page, configured here."""

from __future__ import annotations

from openai_harness import http
from openai_harness.chat import HarnessChatServer

# What the shared page shows for this agent; __main__ adds the model, namespaces and prompts.
UI = {
    "title": "SRE agent",
    "icon": "pulse",
    "new_label": "New investigation",
    "greeting": "Investigate unhealthy workloads with read-only cluster access and an isolated workspace",
    "intro": "The agent reads the cluster through harness tools with a read-only ServiceAccount. Every read is saved as "
             "evidence in the conversation's own Agent Substrate Actor, where the agent analyzes it; the Actor has no "
             "cluster access or credentials. After every turn the server checks each quote in findings.json against "
             "its own copy of the evidence. The agent proposes fixes; it cannot apply them.",
    "follow_ups": [
        {"label": "How would I fix it?", "prompt": "For each finding, propose the smallest fix as commands I could run "
                                                   "myself. Don't run anything."},
        {"label": "What else could explain it?", "prompt": "What else could explain these failures? Check the evidence "
                                                           "for anything you ruled out too quickly."},
    ],
}


def create_app(server: HarnessChatServer, *, config: dict, port: int, jaeger_url: str | None, capacity=None):
    return http.ui_application(server, config={**UI, **config}, port=port, jaeger_url=jaeger_url, capacity=capacity)
