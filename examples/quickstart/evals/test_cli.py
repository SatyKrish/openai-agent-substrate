# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

import sys

import pytest

from examples.quickstart import main as demo


def test_default_command_does_not_start_a_host_execution_server(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["quickstart"])

    def reject(**kwargs):
        pytest.fail("Host execution requires explicit --backend local")

    monkeypatch.setattr(demo, "LocalExecutionServer", reject)
    with pytest.raises(SystemExit) as error:
        demo.main()
    assert error.value.code == 2


def test_local_execution_is_explicit():
    assert demo.parse_args(["--context", "kind-test"]).backend == "substrate"
    assert demo.parse_args(["--backend", "local"]).backend == "local"
