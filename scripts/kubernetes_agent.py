#!/usr/bin/env python3
# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Import .env, build the trusted harness, and run Secret-backed Kubernetes Jobs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ROOT / "deploy/kubernetes"
NAMESPACE = "openai-agent-substrate"
SECRET = "openai-agent-env"
PIN = "6949841e9dc98edbd2e0042ef95c19b4ce20aef5"


def checked(command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def kubectl(args, *command, **kwargs):
    return checked(["kubectl", "--context", args.context, "--kubeconfig", str(args.kubeconfig), *command], **kwargs)


def apply(args, resource):
    # Use stdin, never command-line secret values or a persisted Secret manifest.
    return kubectl(args, "apply", "--server-side", "--field-manager=openai-agent-substrate", "-f", "-", "-o", "name",
                   input=json.dumps(resource).encode(), capture_output=True)


def import_secret(args):
    if not args.env_file.is_file():
        raise ValueError("No .env file found. Copy .env.example to .env and fill in the configuration.")
    # kubectl parses plain KEY=VALUE lines; .env is never executed as a shell script.
    try:
        result = kubectl(args, "create", "secret", "generic", args.secret_name, "-n", NAMESPACE,
                         "--from-env-file=" + str(args.env_file), "--dry-run=client", "-o", "json", capture_output=True)
        resource = json.loads(result.stdout)
    except subprocess.CalledProcessError:
        # Parser errors can contain the offending line. Do not expose it.
        raise ValueError("Cannot parse .env; use plain KEY=VALUE lines without export statements") from None
    if not resource.get("data"):
        raise ValueError("The environment file contains no configuration entries")
    apply(args, dict(apiVersion="v1", kind="Namespace", metadata=dict(name=NAMESPACE)))
    resource["metadata"]["labels"] = {"app.kubernetes.io/managed-by": "openai-agent-substrate"}
    try:
        apply(args, resource)
    except subprocess.CalledProcessError:
        raise RuntimeError("Could not apply the agent configuration Secret; values were not logged") from None
    print(f"Imported configuration into {NAMESPACE}/{args.secret_name}; values omitted.")


def download_kubectl(destination, architecture):
    version = "v1.37.0"
    base = f"https://dl.k8s.io/release/{version}/bin/linux/{architecture}/kubectl"
    with urllib.request.urlopen(base + ".sha256", timeout=60) as response:
        expected = response.read(256).decode().strip()
    if not destination.exists() or hashlib.sha256(destination.read_bytes()).hexdigest() != expected:
        with urllib.request.urlopen(base, timeout=120) as response, destination.open("wb") as handle:
            shutil.copyfileobj(response, handle)
    if hashlib.sha256(destination.read_bytes()).hexdigest() != expected:
        raise RuntimeError("kubectl download checksum mismatch")
    destination.chmod(0o755)


def build(args):
    if checked(["git", "-C", str(args.source_dir), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip() != PIN:
        raise ValueError(f"Substrate source must be checked out at {PIN}")
    checked(["git", "-C", str(args.source_dir), "diff", "--quiet"])
    nodes = json.loads(kubectl(args, "get", "nodes", "-o", "json", capture_output=True).stdout)["items"]
    architectures = {node["status"]["nodeInfo"]["architecture"] for node in nodes}
    if len(architectures) != 1 or not architectures <= {"amd64", "arm64"}:
        raise ValueError("The local harness builder requires a homogeneous amd64 or arm64 cluster")
    architecture = architectures.pop()
    context = ROOT / "artifacts/local/agent-build"
    if context.exists():
        shutil.rmtree(context)
    context.mkdir(parents=True, exist_ok=True)
    binaries = ROOT / "artifacts/local/agent-tools" / ("linux-" + architecture)
    binaries.mkdir(parents=True, exist_ok=True)
    # Allowlist the build context. Local .env and kubeconfig never enter the image.
    for name in ["pyproject.toml", "uv.lock", "README.md", "LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md",
                 "third_party/substrate-sandbox/LICENSE", "deploy/kubernetes/run_agent.py"]:
        target = context / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    for name in ["src", "examples"]:
        target = context / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(ROOT / name, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info", ".env", ".env.*"))
    shutil.copyfile(MANIFESTS / "Dockerfile", context / "Dockerfile")
    shutil.copyfile(args.source_dir / "LICENSE", context / "substrate-LICENSE")
    print(f"Building Linux/{architecture} kubectl-ate from pinned Substrate source.", flush=True)
    checked(["go", "build", "-trimpath", "-ldflags=-s -w", "-o", str(binaries / "kubectl-ate"), "./cmd/kubectl-ate"],
            cwd=args.source_dir, env=dict(os.environ, GOOS="linux", GOARCH=architecture, CGO_ENABLED="0"))
    download_kubectl(binaries / "kubectl", architecture)
    for name in ["kubectl", "kubectl-ate"]:
        shutil.copyfile(binaries / name, context / name)
        (context / name).chmod(0o755)
    docker = ["docker"] + (["--context", args.docker_context] if args.docker_context else [])
    checked([*docker, "build", "--provenance=false", "--platform", f"linux/{architecture}", "-t", args.image, str(context)])
    checked([*docker, "push", args.image])
    digests = json.loads(checked([*docker, "image", "inspect", args.image, "--format", "{{json .RepoDigests}}"], capture_output=True).stdout)
    repository = args.image.rsplit(":", 1)[0] if ":" in args.image.rsplit("/", 1)[-1] else args.image
    image = next(d for d in digests if d.startswith(repository + "@sha256:"))
    apply(args, json.loads((MANIFESTS / "resources.json").read_text()))
    args.state_file.parent.mkdir(parents=True, exist_ok=True)
    args.state_file.write_text(json.dumps(dict(image=image, context=args.context), indent=2) + "\n")
    print("Trusted harness image and Kubernetes resources are ready; no model call was made.")


def make_job(image, template, model=None, secret_name=SECRET):
    job = json.loads((MANIFESTS / "agent-job.json").read_text())
    job["metadata"]["name"] = "openai-agent-" + uuid.uuid4().hex[:12]
    container = job["spec"]["template"]["spec"]["containers"][0]
    container["image"] = image
    container["env"][0]["value"] = template
    container["envFrom"][0]["secretRef"]["name"] = secret_name
    job["spec"]["template"]["spec"]["volumes"][0]["secret"]["secretName"] = secret_name
    if model:
        container["args"] = ["--model", model]
    return job


def run_job(args):
    state = json.loads(args.state_file.read_text())
    if state["context"] != args.context:
        raise ValueError("Harness setup belongs to a different Kubernetes context; run build for this cluster")
    # Require the Secret before creating a pod; never display its data.
    kubectl(args, "get", "secret", args.secret_name, "-n", NAMESPACE, "-o", "name", capture_output=True)
    job = make_job(state["image"], args.template, args.model, args.secret_name)
    apply(args, job)
    name = job["metadata"]["name"]
    print(f"Created {NAMESPACE}/{name}", flush=True)
    if args.wait:
        wait_for_job(args, name)
    return name


def wait_for_job(args, name):
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        status = json.loads(kubectl(args, "get", "job", name, "-n", NAMESPACE, "-o", "json", capture_output=True).stdout).get("status", {})
        if status.get("succeeded") or status.get("failed"):
            kubectl(args, "logs", "job/" + name, "-n", NAMESPACE, "--tail=150")
            if status.get("failed"):
                raise RuntimeError(f"Agent Job {name} failed")
            print(f"Agent Job {name} completed. Artifacts are retained on PVC openai-agent-artifacts.")
            return
        time.sleep(2)
    raise TimeoutError(f"Timed out waiting for Agent Job {name}")


def collect(args):
    state = json.loads(args.state_file.read_text())
    if state["context"] != args.context:
        raise ValueError("Harness setup belongs to a different Kubernetes context")
    name = "openai-agent-reader-" + uuid.uuid4().hex[:12]
    pod = dict(apiVersion="v1", kind="Pod", metadata=dict(name=name, namespace=NAMESPACE), spec=dict(
        automountServiceAccountToken=False, restartPolicy="Never",
        securityContext=dict(runAsNonRoot=True, runAsUser=10001, runAsGroup=10001, fsGroup=10001),
        containers=[dict(name="reader", image=state["image"], command=["python", "-c", "import time; time.sleep(600)"],
            securityContext=dict(allowPrivilegeEscalation=False, readOnlyRootFilesystem=True, capabilities=dict(drop=["ALL"])),
            volumeMounts=[dict(name="artifacts", mountPath="/artifacts", readOnly=True)])],
        volumes=[dict(name="artifacts", persistentVolumeClaim=dict(claimName="openai-agent-artifacts", readOnly=True))]))
    try:
        apply(args, pod)
        kubectl(args, "wait", "--for=condition=Ready", "pod/" + name, "-n", NAMESPACE, "--timeout=120s", capture_output=True)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        kubectl(args, "cp", "-n", NAMESPACE, name + ":/artifacts/.", str(args.output_dir))
        print(f"Collected agent artifacts into {args.output_dir}")
    finally:
        kubectl(args, "delete", "pod", name, "-n", NAMESPACE, "--ignore-not-found", capture_output=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["secret", "build", "run", "collect"])
    parser.add_argument("--context", required=True)
    parser.add_argument("--kubeconfig", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--secret-name", default=SECRET)
    parser.add_argument("--source-dir", type=Path, default=ROOT / "artifacts/local/substrate")
    parser.add_argument("--image", default="localhost:5501/openai-agent-substrate-harness:local")
    parser.add_argument("--docker-context")
    parser.add_argument("--state-file", type=Path, default=ROOT / "artifacts/local/agent-deployment.json")
    parser.add_argument("--template", help="Python-capable ActorTemplate; required for run")
    parser.add_argument("--model", help="Override the mounted OPENAI_MODEL; scripted performs no model inference")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/local/agent-results")
    args = parser.parse_args()
    if args.action == "run" and not args.template:
        parser.error("run requires --template")
    {"secret": import_secret, "build": build, "run": run_job, "collect": collect}[args.action](args)


if __name__ == "__main__":
    main()
