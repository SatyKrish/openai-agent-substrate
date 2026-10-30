# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""The host check: runs in the harness after each turn, on copies of the files the agent wrote.

A real agent checks its outputs against something it trusts (see the other
examples). This one only confirms that each output file is well formed.
"""

from __future__ import annotations

import csv
import io
import json


def check(conversation, turn: dict, files: dict[str, bytes]) -> dict:
    outputs = {path: data for path, data in files.items() if path.startswith("output/")}
    if not outputs:
        return {"status": "no_output"}
    for path, data in sorted(outputs.items()):
        try:
            text = data.decode("utf-8")
            if not text.strip():
                raise ValueError("it is empty")
            if path.endswith(".json"):
                json.loads(text)
            elif path.endswith(".csv") and len(next(csv.reader(io.StringIO(text)))) < 1:
                raise ValueError("it has no header row")
        except (UnicodeDecodeError, ValueError, StopIteration) as error:
            return {"status": "failed", "detail": f"{path} is not well formed: {error}"}
    return {"status": "passed", "files": sorted(outputs)}
