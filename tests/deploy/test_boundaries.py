# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def imports(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module.split(".")[0]


def test_library_has_no_repository_application_dependencies():
    for path in (ROOT / "src").rglob("*.py"):
        assert not {"deploy", "examples", "scripts"}.intersection(imports(path)), path


def test_kubernetes_base_is_independent_of_applications_and_sdk():
    for path in (ROOT / "deploy/kubernetes/base").glob("*.py"):
        assert not {"examples", "scripts", "agents", "openai_agent_substrate"}.intersection(imports(path)), path


def test_repository_python_modules_do_not_import_shell_scripts():
    for directory in ("src", "deploy", "examples", "tests"):
        for path in (ROOT / directory).rglob("*.py"):
            assert "scripts" not in set(imports(path)), path
