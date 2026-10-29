# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

import pytest

from examples.document_intelligence.agent.lib.configuration import select_model
from examples.document_intelligence.main import parse_args


def test_default_uses_configured_live_model(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "configured-openai-model")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used-for-network")
    args = parse_args(["--context", "kind-test", "--template", "python-template"])
    assert args.backend == "substrate"
    assert args.model == "configured-openai-model"


def test_missing_api_key_does_not_fall_back_to_scripted(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "configured-openai-model")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        select_model()


def test_environment_cannot_silently_select_test_mode(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "scripted")
    with pytest.raises(ValueError, match="explicit --test"):
        select_model()
    assert parse_args(["--test"]).model == "scripted"


def test_explicit_evaluation_needs_no_model_credentials(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    args = parse_args(["--test"])
    assert args.model == "scripted" and args.backend == "local"
