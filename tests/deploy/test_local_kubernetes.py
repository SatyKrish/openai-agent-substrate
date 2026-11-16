# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

"""Exercise local setup with stand-ins that never touch a real cluster."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
PIN = (ROOT / ".substrate-revision").read_text().strip()

MOCK_TOOL = """#!/bin/bash
set -e
tool="${0##*/}"
printf '%s %s\\n' "$tool" "$*" >> "$SUBSTRATE_TEST_CALLS"
case "$tool:$*" in
  uname:-s) echo Linux ;;
  git:*"rev-parse"*) echo "$SUBSTRATE_TEST_PIN" ;;
  kind.sh:"get clusters")
    case "$SUBSTRATE_TEST_CLUSTER" in
      error) exit 91 ;;
      existing) echo openai-agent-substrate ;;
    esac
    ;;
  docker:"inspect kind-registry") exit 1 ;;
  kubectl:*"config view --minify"*) printf '%s' "$SUBSTRATE_TEST_API_SERVER" ;;
  kubectl:*"get namespace kube-system"*)
    [[ "$SUBSTRATE_TEST_REVISION" != error ]] || exit 92
    printf '%s' "$SUBSTRATE_TEST_REVISION"
    ;;
  kubectl:*"get namespace ate-system"*)
    if [[ "$SUBSTRATE_TEST_INSTALLED" == 1 ]]; then echo namespace/ate-system; fi
    ;;
  uv:*"artifacts/examples/starter/deployment/deployment.json"*) echo starter-test-template ;;
  uv:*"sandbox/tests/kubernetes/test_worker_eviction.py"*)
    printf 'eviction %s %s %s\\n' "$SUBSTRATE_RUN_WORKER_EVICTION_TESTS" "$SUBSTRATE_KUBE_CONTEXT" "$SUBSTRATE_EVICTION_TEMPLATE" >> "$SUBSTRATE_TEST_CALLS"
    ;;
esac
"""


@pytest.fixture
def setup_script(tmp_path):
    script = tmp_path / "scripts/local-kubernetes.sh"
    script.parent.mkdir()
    shutil.copyfile(ROOT / "scripts/local-kubernetes.sh", script)
    (tmp_path / ".substrate-revision").write_text(PIN + "\n")
    source = tmp_path / "artifacts/local" / f"substrate-{PIN}"
    (source / ".git").mkdir(parents=True)
    tools = tmp_path / "tools"
    for path in [*(tools / name for name in ("uname", "git", "docker", "kubectl", "go", "uv")),
                 *(source / "hack" / name for name in ("kind.sh", "create-kind-cluster.sh"))]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(MOCK_TOOL)
        path.chmod(0o755)

    def run(command="setup", *, cluster="existing", installed=False, revision="", source_dir="",
            api_server="https://127.0.0.1:7443"):
        calls = tmp_path / "calls"
        env = dict(os.environ, PATH=f"{tools}:{os.defpath}", SUBSTRATE_SOURCE_DIR=source_dir,
                   SUBSTRATE_TEST_PIN=PIN, SUBSTRATE_TEST_CALLS=str(calls),
                   SUBSTRATE_TEST_CLUSTER=cluster, SUBSTRATE_TEST_REVISION=revision,
                   SUBSTRATE_TEST_INSTALLED=str(int(installed)), SUBSTRATE_TEST_API_SERVER=api_server)
        result = subprocess.run(["bash", str(script), command], env=env, capture_output=True, text=True)
        return result, calls.read_text()

    return run


@pytest.mark.parametrize(("cluster", "installed", "revision", "error"), [
    ("error", False, "", 91),
    ("existing", False, "error", 92),
    ("existing", True, "", 1),
    ("existing", True, "another-revision", 1),
])
def test_setup_never_creates_or_deploys_when_cluster_state_is_unknown_or_incompatible(
        setup_script, cluster, installed, revision, error):
    result, calls = setup_script(cluster=cluster, installed=installed, revision=revision)
    assert result.returncode == error
    assert "create-kind-cluster.sh" not in calls
    assert "deploy ate-system" not in calls


@pytest.mark.parametrize(("cluster", "installed", "revision"), [
    ("absent", False, ""),
    ("existing", True, f"{PIN}-pending"),
    ("existing", True, PIN),
])
def test_setup_installs_gvisor_and_retries_partial_setup(setup_script, cluster, installed, revision):
    result, calls = setup_script(cluster=cluster, installed=installed, revision=revision)
    assert result.returncode == 0, result.stderr
    assert ("create-kind-cluster.sh" in calls) == (cluster == "absent")
    assert ("Ready pod --all" in calls) == (revision == PIN)
    if cluster == "absent":
        assert calls.index(f"substrate-revision={PIN}-pending") < calls.index("deploy ate-system")
    assert "deploy ate-system --sandbox-classes=gvisor" in calls
    assert f"substrate-revision={PIN} --overwrite" in calls


def test_setup_preserves_a_populated_non_git_source_override(setup_script, tmp_path):
    source = tmp_path / "external-source"
    source.mkdir()
    (source / "data").touch()
    result, calls = setup_script(source_dir=str(source))
    assert result.returncode == 1
    assert "git init" not in calls
    assert "existing Git checkout" in result.stderr


def test_regular_provider_check_runs_only_the_standard_cluster_test(setup_script):
    result, calls = setup_script("test")
    assert result.returncode == 0, result.stderr
    assert "pytest -q sandbox/tests/kubernetes/test_live_cluster.py" in calls
    assert "test_worker_eviction.py" not in calls


def test_worker_eviction_check_requires_the_pinned_test_cluster(setup_script):
    result, calls = setup_script("worker-eviction-test", revision=f"{PIN}-pending")
    assert result.returncode == 1
    assert "dedicated Kind cluster" in result.stderr
    assert "examples.starter.deploy" not in calls
    assert "test_worker_eviction.py" not in calls


def test_worker_eviction_check_rejects_a_nonlocal_api_before_deploy(setup_script):
    result, calls = setup_script("worker-eviction-test", api_server="https://remote.example:7443", revision=PIN)
    assert result.returncode == 1
    assert "loopback Kind API endpoint" in result.stderr
    assert "get namespace kube-system" not in calls
    assert "examples.starter.deploy" not in calls


def test_worker_eviction_check_deploys_the_image_and_opts_into_the_test(setup_script):
    result, calls = setup_script("worker-eviction-test", revision=PIN)
    assert result.returncode == 0, result.stderr
    assert calls.index("examples.starter.deploy sandbox") < calls.index("test_worker_eviction.py")
    assert "eviction 1 kind-openai-agent-substrate starter-test-template" in calls
