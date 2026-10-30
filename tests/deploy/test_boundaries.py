# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LIBRARY = ROOT / "sandbox/src"
HARNESS = ROOT / "harness/src"


def imports(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module.split(".")[0]


def test_library_has_no_repository_application_dependencies():
    for path in LIBRARY.rglob("*.py"):
        assert not {"deploy", "examples", "scripts", "harness"}.intersection(imports(path)), path


def test_harness_depends_on_the_library_but_not_on_agents_or_deployment():
    for path in HARNESS.rglob("*.py"):
        assert not {"deploy", "examples", "scripts"}.intersection(imports(path)), path


def test_harness_and_templates_agree_on_the_workspace_root():
    def constant(path):
        tree = ast.parse(path.read_text())
        return next(node.value.value for node in tree.body if isinstance(node, ast.Assign)
                    and any(getattr(target, "id", None) == "WORKSPACE_ROOT" for target in node.targets))

    assert constant(HARNESS / "harness/spec.py") == constant(ROOT / "deploy/kubernetes/sandbox.py") == "/workspace"


def test_kubernetes_base_is_independent_of_applications_and_sdk():
    for path in (ROOT / "deploy/kubernetes/base").glob("*.py"):
        assert not {"examples", "scripts", "agents", "openai_agent_substrate"}.intersection(imports(path)), path


def test_repository_python_modules_do_not_import_shell_scripts():
    for directory in ("sandbox", "harness", "deploy", "examples", "tests"):
        for path in (ROOT / directory).rglob("*.py"):
            assert "scripts" not in set(imports(path)), path


def test_library_tests_have_no_application_dependencies():
    for directory in ("unit", "integration", "kubernetes"):
        for path in (ROOT / "tests" / directory).rglob("*.py"):
            assert not {"examples", "deploy", "scripts"}.intersection(imports(path)), path


def test_library_contains_no_document_intelligence_operation_names():
    for path in [*LIBRARY.rglob("*.py"), *HARNESS.rglob("*.py")]:
        assert "document_intelligence." not in path.read_text(), path
