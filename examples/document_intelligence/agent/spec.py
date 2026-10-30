# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""What the shared harness needs beside the SandboxAgent: the starting workspace, uploads and the host check."""

from __future__ import annotations

import uuid

from openai_harness.spec import MIB, AgentSpec, Uploads

from .agent import AGENT_ROOT, build_agent
from .lib import citations, pdf
from .sandbox.sandbox import build_manifest, stage_inputs

INPUTS = stage_inputs()
FOLLOW_UP = (AGENT_ROOT / "follow-up.md").read_text(encoding="utf-8").strip()
UPLOADS_ONLY = (AGENT_ROOT / "uploads-only.md").read_text(encoding="utf-8").strip()
# The panel opens this file after a turn that changed it.
REPORT = "output/comparison.md"


def start(conversation, has_uploads: bool, root: str):
    """A conversation that starts with uploads gets an empty workspace; otherwise the sample reports."""
    meta = conversation.metadata
    meta.setdefault("analysis_id", str(uuid.uuid4()))
    meta.setdefault("sources", "uploads" if has_uploads else "samples")
    samples = meta["sources"] == "samples"
    return build_manifest(INPUTS if samples else {}, meta["analysis_id"], root)


def instructions(conversation) -> str:
    return FOLLOW_UP if conversation.metadata.get("sources") == "samples" else FOLLOW_UP + "\n\n" + UPLOADS_ONLY


SPEC = AgentSpec(
    name="document_intelligence",
    build_agent=build_agent,
    start=start,
    instructions=instructions,
    uploads=Uploads(
        types={".md": "text/markdown", ".txt": "text/plain", ".csv": "text/csv", ".pdf": "application/pdf"},
        hint="Upload PDF, Markdown, text or CSV files (.pdf, .md, .txt, .csv).",
        max_bytes=10 * MIB, max_count=5,
        signatures={".pdf": (b"%PDF-", "This file is not a PDF.")},
        processors={".pdf": pdf.PROCESSOR},
    ),
    check=lambda conversation, turn, files: citations.check_turn(conversation, files, INPUTS),
    default_question="Summarize the uploaded documents, citing exact quotes.",
)
