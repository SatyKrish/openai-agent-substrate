#!/usr/bin/env bash
# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
umask 077

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOCAL_DIR="$PROJECT_ROOT/artifacts/local"
SOURCE_DIR="${SUBSTRATE_SOURCE_DIR:-$LOCAL_DIR/substrate}"
PIN=6949841e9dc98edbd2e0042ef95c19b4ce20aef5
CLUSTER=openai-agent-substrate
PROFILE=openai-agent-substrate
export KUBECONFIG="$LOCAL_DIR/kubeconfig"
export KIND_CLUSTER_NAME="$CLUSTER"
export KIND_REGISTRY_PORT=5501
export ATE_INSTALL_KIND=true
export KO_DOCKER_REPO=localhost:5501
export PATH="$LOCAL_DIR/bin:$PATH"
mkdir -p "$LOCAL_DIR/bin"
cd "$PROJECT_ROOT"

configure_docker() {
  if [[ "$(uname -s)" == Darwin ]]; then
    export DOCKER_HOST="unix://$HOME/.colima/$PROFILE/docker.sock"
    unset DOCKER_CONTEXT
  fi
}

prepare_source() {
  if [[ ! -d "$SOURCE_DIR/.git" ]]; then
    git clone --filter=blob:none --no-checkout https://github.com/agent-substrate/substrate.git "$SOURCE_DIR"
    git -C "$SOURCE_DIR" fetch origin "$PIN"
    git -C "$SOURCE_DIR" checkout --detach "$PIN"
  fi
  [[ "$(git -C "$SOURCE_DIR" rev-parse HEAD)" == "$PIN" ]] || { echo "Source checkout must be at $PIN" >&2; exit 1; }
  git -C "$SOURCE_DIR" diff --quiet
}

case "${1:-help}" in
  setup)
    for tool in git go docker kubectl uv; do command -v "$tool" >/dev/null; done
    if [[ "$(uname -s)" == Darwin ]]; then
      command -v colima >/dev/null
      if ! colima status --profile "$PROFILE" >/dev/null 2>&1; then
        colima start --profile "$PROFILE" --cpus 6 --memory 12 --disk 60 --activate=false --ssh-config=false
      fi
    fi
    configure_docker
    docker info >/dev/null
    prepare_source
    if ! (cd "$SOURCE_DIR" && hack/kind.sh get clusters) | grep -Fxq "$CLUSTER"; then
      if docker inspect kind-registry >/dev/null 2>&1; then
        docker port kind-registry | grep -q ':5501' || { echo "kind-registry already uses another port; use a dedicated Docker daemon." >&2; exit 1; }
      fi
      (cd "$SOURCE_DIR" && hack/create-kind-cluster.sh) 2>&1 | tee "$LOCAL_DIR/cluster-setup.log"
    else
      (cd "$SOURCE_DIR" && hack/kind.sh export kubeconfig --name "$CLUSTER" --kubeconfig "$KUBECONFIG")
    fi
    (cd "$SOURCE_DIR" && go build -o "$LOCAL_DIR/bin/kubectl-ate" ./cmd/kubectl-ate)
    (cd "$SOURCE_DIR" && go run ./cmd/ate-setup --no-dev-env --rollout-timeout 300s deploy ate-system --credential-provider='{"enabled":false}' --ingress-allowed-spiffe-ids=spiffe://cluster.local/ns/ate-system/sa/atenet-client) 2>&1 | tee "$LOCAL_DIR/substrate-setup.log"
    (cd "$SOURCE_DIR" && go run ./cmd/ate-setup --no-dev-env --rollout-timeout 300s deploy demo sandbox) 2>&1 | tee "$LOCAL_DIR/sandbox-setup.log"
    uv sync --locked
    if [[ -f "$PROJECT_ROOT/.env" ]]; then
      uv run --locked python scripts/kubernetes_agent.py secret --context "kind-$CLUSTER" --kubeconfig "$KUBECONFIG"
    else
      echo "No .env found. Copy .env.example to .env to configure the Kubernetes agent."
    fi
    ;;
  test)
    uv run --locked openai-agent-substrate --backend substrate --context "kind-$CLUSTER" --kubeconfig "$KUBECONFIG" --output-dir "$LOCAL_DIR/results"
    SUBSTRATE_RUN_KUBERNETES_TESTS=1 SUBSTRATE_KUBE_CONTEXT="kind-$CLUSTER" SUBSTRATE_KUBECONFIG="$KUBECONFIG" uv run --locked pytest -q tests/kubernetes
    ;;
  document-intelligence)
    configure_docker
    uv run --locked python -m examples.document_intelligence.deploy --context "kind-$CLUSTER" --kubeconfig "$KUBECONFIG" --image localhost:5501/openai-agent-substrate-document-intelligence:local
    DOCUMENT_TEMPLATE="$(uv run --locked python -c 'import json; print(json.load(open("artifacts/examples/document_intelligence/deployment/deployment.json"))["template"])')"
    uv run --locked python -m examples.document_intelligence --test --backend substrate --context "kind-$CLUSTER" --kubeconfig "$KUBECONFIG" --template "$DOCUMENT_TEMPLATE" --output-dir "$LOCAL_DIR/document-intelligence"
    SUBSTRATE_RUN_KUBERNETES_TESTS=1 SUBSTRATE_KUBE_CONTEXT="kind-$CLUSTER" SUBSTRATE_KUBECONFIG="$KUBECONFIG" SUBSTRATE_DOCUMENT_TEMPLATE="$DOCUMENT_TEMPLATE" uv run --locked pytest -q tests/kubernetes/test_document_intelligence.py
    ;;
  agent-setup)
    configure_docker
    prepare_source
    uv run --locked python scripts/kubernetes_agent.py secret --context "kind-$CLUSTER" --kubeconfig "$KUBECONFIG"
    uv run --locked python -m examples.document_intelligence.deploy --context "kind-$CLUSTER" --kubeconfig "$KUBECONFIG" --image localhost:5501/openai-agent-substrate-document-intelligence:local
    uv run --locked python scripts/kubernetes_agent.py build --context "kind-$CLUSTER" --kubeconfig "$KUBECONFIG" --source-dir "$SOURCE_DIR"
    ;;
  agent-run|agent-test)
    uv run --locked python scripts/kubernetes_agent.py secret --context "kind-$CLUSTER" --kubeconfig "$KUBECONFIG"
    DOCUMENT_TEMPLATE="$(uv run --locked python -c 'import json; print(json.load(open("artifacts/examples/document_intelligence/deployment/deployment.json"))["template"])')"
    AGENT_COMMAND=(uv run --locked python scripts/kubernetes_agent.py run --context "kind-$CLUSTER" --kubeconfig "$KUBECONFIG" --template "$DOCUMENT_TEMPLATE" --wait)
    if [[ "$1" == agent-test ]]; then AGENT_COMMAND+=(--model scripted); fi
    "${AGENT_COMMAND[@]}"
    ;;
  agent-artifacts)
    uv run --locked python scripts/kubernetes_agent.py collect --context "kind-$CLUSTER" --kubeconfig "$KUBECONFIG"
    ;;
  status)
    kubectl --context "kind-$CLUSTER" get nodes
    kubectl --context "kind-$CLUSTER" get pods -n ate-system
    kubectl --context "kind-$CLUSTER" get workerpools,pods -n ate-demo-sandbox
    ;;
  stop)
    [[ "$(uname -s)" == Darwin ]] || { echo "Stop your Linux container runtime manually when appropriate." >&2; exit 1; }
    colima stop --profile "$PROFILE"
    ;;
  *)
    echo "Usage: $0 {setup|test|document-intelligence|agent-setup|agent-test|agent-run|agent-artifacts|status|stop}"
    echo "Uses an isolated Kind cluster and project-local kubeconfig. setup reuses an existing cluster."
    ;;
esac
