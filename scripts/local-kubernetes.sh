#!/usr/bin/env bash
# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
umask 077

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOCAL_DIR="$PROJECT_ROOT/artifacts/local"
SOURCE_DIR="${SUBSTRATE_SOURCE_DIR:-$LOCAL_DIR/substrate}"
PIN="$(cat "$PROJECT_ROOT/.substrate-revision")"
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
    # Agent Substrate's quickstart (docs/get-started/quickstart.md), step by step:
    # step 1 created the cluster and registry above; step 2 is ATE_INSTALL_KIND, exported at the top.
    # Step 5's CLI is built into artifacts/local/bin, not `go install`ed, so it matches the pinned commit
    # and leaves any other kubectl-ate on this machine alone.
    (cd "$SOURCE_DIR" && go build -o "$LOCAL_DIR/bin/kubectl-ate" ./cmd/kubectl-ate)
    # Step 3, with the quickstart's flags. The bundled credential provider starts with a default-deny
    # policy, and no atespace here is granted any namespace, so sandboxes receive no credentials.
    # --no-dev-env ignores a personal .ate-dev-env.sh in the checkout; Colima needs the longer rollout timeout.
    (cd "$SOURCE_DIR" && go run ./cmd/ate-setup --no-dev-env --rollout-timeout 300s deploy ate-system --credential-provider='{"name":"k8s.io"}' --ingress-allowed-spiffe-ids=spiffe://cluster.local/ns/ate-system/sa/atenet-client) 2>&1 | tee "$LOCAL_DIR/substrate-setup.log"
    # Step 4 deploys a sample application. The sandbox demo replaces the counter: its WorkerPool
    # (workload=sandbox) runs every agent's Actors, and the provider check uses its template.
    (cd "$SOURCE_DIR" && go run ./cmd/ate-setup --no-dev-env --rollout-timeout 300s deploy demo sandbox) 2>&1 | tee "$LOCAL_DIR/sandbox-setup.log"
    uv sync --locked
    if [[ -f "$PROJECT_ROOT/.env" ]]; then
      uv run --locked python -m examples.document_intelligence.deploy secret --context "kind-$CLUSTER" --kubeconfig "$KUBECONFIG"
    else
      echo "No .env found. Copy .env.example to .env to configure the Kubernetes agent."
    fi
    ;;
  test)
    uv run --locked python -m examples.provider_check --backend substrate --context "kind-$CLUSTER" --kubeconfig "$KUBECONFIG" --output-dir "$LOCAL_DIR/results"
    SUBSTRATE_RUN_KUBERNETES_TESTS=1 SUBSTRATE_KUBE_CONTEXT="kind-$CLUSTER" SUBSTRATE_KUBECONFIG="$KUBECONFIG" uv run --locked pytest -q tests/kubernetes
    ;;
  document-intelligence)
    configure_docker
    uv run --locked python -m examples.document_intelligence.deploy sandbox --context "kind-$CLUSTER" --kubeconfig "$KUBECONFIG" --image localhost:5501/openai-agent-substrate-document-intelligence:local
    DOCUMENT_TEMPLATE="$(uv run --locked python -c 'import json; print(json.load(open("artifacts/examples/document_intelligence/deployment/deployment.json"))["template"])')"
    uv run --locked python -m examples.document_intelligence --test --backend substrate --context "kind-$CLUSTER" --kubeconfig "$KUBECONFIG" --template "$DOCUMENT_TEMPLATE" --output-dir "$LOCAL_DIR/document-intelligence"
    SUBSTRATE_RUN_KUBERNETES_TESTS=1 SUBSTRATE_KUBE_CONTEXT="kind-$CLUSTER" SUBSTRATE_KUBECONFIG="$KUBECONFIG" SUBSTRATE_DOCUMENT_TEMPLATE="$DOCUMENT_TEMPLATE" uv run --locked pytest -q examples/document_intelligence/evals/kubernetes/test_document_intelligence.py
    ;;
  agent-setup)
    configure_docker
    prepare_source
    uv run --locked python -m examples.document_intelligence.deploy secret --context "kind-$CLUSTER" --kubeconfig "$KUBECONFIG"
    uv run --locked python -m examples.document_intelligence.deploy sandbox --context "kind-$CLUSTER" --kubeconfig "$KUBECONFIG" --image localhost:5501/openai-agent-substrate-document-intelligence:local
    uv run --locked python -m examples.document_intelligence.deploy build --context "kind-$CLUSTER" --kubeconfig "$KUBECONFIG" --source-dir "$SOURCE_DIR"
    ;;
  agent-run|agent-test)
    uv run --locked python -m deploy.kubernetes.base resources --context "kind-$CLUSTER" --kubeconfig "$KUBECONFIG"
    uv run --locked python -m examples.document_intelligence.deploy secret --context "kind-$CLUSTER" --kubeconfig "$KUBECONFIG"
    DOCUMENT_TEMPLATE="$(uv run --locked python -c 'import json; print(json.load(open("artifacts/examples/document_intelligence/deployment/deployment.json"))["template"])')"
    AGENT_COMMAND=(uv run --locked python -m examples.document_intelligence.deploy run --context "kind-$CLUSTER" --kubeconfig "$KUBECONFIG" --template "$DOCUMENT_TEMPLATE" --wait)
    if [[ "$1" == agent-test ]]; then AGENT_COMMAND+=(--model scripted); fi
    "${AGENT_COMMAND[@]}"
    ;;
  tracing-setup|tracing-ui)
    TRACING_ACTION=setup
    if [[ "$1" == tracing-ui ]]; then TRACING_ACTION=ui; fi
    uv run --locked python -m examples.document_intelligence.deploy.jaeger "$TRACING_ACTION" --context "kind-$CLUSTER" --kubeconfig "$KUBECONFIG"
    ;;
  agent-artifacts)
    uv run --locked python -m examples.document_intelligence.deploy collect --context "kind-$CLUSTER" --kubeconfig "$KUBECONFIG"
    ;;
  ui|sre-ui|starter-ui|starter-chat)
    # The UI server is the trusted harness: it runs on this host with .env's OPENAI_* settings.
    UI_ENV=(OPENAI_AGENTS_DISABLE_TRACING=1)
    if kubectl --context "kind-$CLUSTER" -n openai-agent-substrate get service jaeger-all-in-one >/dev/null 2>&1; then
      # Separate forwards: an already running tracing-ui or UI keeps its ports and the others are added.
      FORWARD_PIDS=()
      for PORT in 4318 16686; do
        kubectl --context "kind-$CLUSTER" -n openai-agent-substrate port-forward service/jaeger-all-in-one "$PORT:$PORT" \
          --address 127.0.0.1 >>"$LOCAL_DIR/ui-port-forward.log" 2>&1 &
        FORWARD_PIDS+=("$!")
      done
      trap 'kill "${FORWARD_PIDS[@]}" 2>/dev/null' EXIT
      UI_ENV=(OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318 OTEL_SERVICE_NAME=openai-agent-substrate)
    else
      echo "Jaeger is not deployed; run tracing-setup for per-turn traces. Starting without tracing." >&2
    fi
    UI_MODULE=examples.document_intelligence.ui
    if [[ "$1" == sre-ui ]]; then UI_MODULE=examples.sre.ui; fi
    if [[ "$1" == starter-ui ]]; then UI_MODULE=examples.starter.ui; fi
    # The golden path's last step: chat in the terminal; starter-ui shows the same conversations.
    if [[ "$1" == starter-chat ]]; then UI_MODULE=examples.starter; fi
    env "${UI_ENV[@]}" uv run --locked --group ui python -m "$UI_MODULE" \
      --context "kind-$CLUSTER" --kubeconfig "$KUBECONFIG" "${@:2}"
    ;;
  starter-setup)
    # The golden path's one agent step: the starter's sandbox image and ActorTemplate.
    configure_docker
    uv run --locked python -m examples.starter.deploy sandbox --context "kind-$CLUSTER" --kubeconfig "$KUBECONFIG"
    ;;
  sre-setup)
    # The SRE sandbox image and template, the demo namespace, then the reader's Roles in it.
    configure_docker
    uv run --locked python -m examples.sre.deploy sandbox --context "kind-$CLUSTER" --kubeconfig "$KUBECONFIG"
    uv run --locked python -m examples.sre.deploy demo --context "kind-$CLUSTER" --kubeconfig "$KUBECONFIG"
    uv run --locked python -m examples.sre.deploy access --context "kind-$CLUSTER" --kubeconfig "$KUBECONFIG" "${@:2}"
    ;;
  status)
    kubectl --context "kind-$CLUSTER" get nodes
    kubectl --context "kind-$CLUSTER" get pods -n ate-system
    kubectl --context "kind-$CLUSTER" get workerpools,pods -n ate-demo-sandbox
    ;;
  teardown)
    # The quickstart's teardown: delete the Kind cluster and its registry. Conversations' Actors go with it.
    configure_docker
    (cd "$SOURCE_DIR" && hack/delete-kind-cluster.sh)
    rm -f "$KUBECONFIG"
    ;;
  stop)
    [[ "$(uname -s)" == Darwin ]] || { echo "Stop your Linux container runtime manually when appropriate." >&2; exit 1; }
    colima stop --profile "$PROFILE"
    ;;
  *)
    echo "Usage: $0 {setup|test|document-intelligence|agent-setup|agent-test|agent-run|agent-artifacts|tracing-setup|tracing-ui|ui|starter-setup|starter-chat|starter-ui|sre-setup|sre-ui|status|stop|teardown}"
    echo "Uses an isolated Kind cluster and project-local kubeconfig. setup reuses an existing cluster."
    ;;
esac
