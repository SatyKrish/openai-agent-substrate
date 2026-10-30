# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
import subprocess
import sys
from pathlib import Path


def test_provider_and_testing_helpers_import_without_otel_sdk_or_exporter():
    source = Path(__file__).resolve().parents[2] / "src"
    code = """
import importlib.abc
import sys
sys.path.insert(0, sys.argv[1])
class NoOpenTelemetry(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'opentelemetry.sdk', 'opentelemetry.exporter'} or fullname.startswith(('opentelemetry.sdk.', 'opentelemetry.exporter.')):
            raise ModuleNotFoundError('OpenTelemetry is not installed', name=fullname)
sys.meta_path.insert(0, NoOpenTelemetry())
from agent_substrate import SubstrateSandboxClient
from agent_substrate.testing.server import LocalExecutionServer
assert not any(name.startswith(('opentelemetry.sdk', 'opentelemetry.exporter')) for name in sys.modules)
"""
    subprocess.run([sys.executable, "-I", "-c", code, str(source)], check=True, capture_output=True)
