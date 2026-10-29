# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Live model selection is the default; evaluation mode is explicitly requested."""

import os


def select_model(requested=None, *, test=False):
    if test or requested == "scripted":
        if test and requested not in (None, "scripted"):
            raise ValueError("--test cannot be combined with a live --model")
        return "scripted"
    model = requested or os.environ.get("OPENAI_MODEL")
    if not model:
        raise ValueError("Set OPENAI_MODEL or pass --model for live OpenAI inference; use --test for an evaluation")
    if model == "scripted":
        raise ValueError("OPENAI_MODEL=scripted requires explicit --test; agent-run expects a live model")
    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("Set OPENAI_API_KEY in the trusted harness environment before running a live model")
    return model
