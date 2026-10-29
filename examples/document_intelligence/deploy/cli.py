# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Document Intelligence image builds, configuration and harness Jobs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import urllib.request
import uuid
from contextlib import suppress

from deploy.kubernetes.base import resources as base
from . import sandbox
from .job import make_job, make_reader_pod

ROOT = Path(__file__).resolve().parents[3]
MANIFESTS = Path(__file__).parent
NAMESPACE = base.NAMESPACE
SECRET = "openai-agent-env"
PIN = (ROOT / ".substrate-revision").read_text().strip()
KUBECTL_RELEASE = json.loads((MANIFESTS / "kubectl-checksums.json").read_text())
checked = base.checked
kubectl = base.kubectl
apply = base.apply


def download_kubectl(destination, architecture):
    version = KUBECTL_RELEASE["version"]
    expected = KUBECTL_RELEASE["sha256"].get(architecture)
    if expected is None:
        raise ValueError("Unsupported kubectl architecture: " + architecture)
    if not destination.exists() or hashlib.sha256(destination.read_bytes()).hexdigest() != expected:
        temporary = destination.with_name(destination.name + ".download-" + uuid.uuid4().hex)
        try:
            base = f"https://dl.k8s.io/release/{version}/bin/linux/{architecture}/kubectl"
            with urllib.request.urlopen(base, timeout=120) as response, temporary.open("wb") as handle:
                shutil.copyfileobj(response, handle)
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != expected:
                raise RuntimeError("kubectl download checksum mismatch")
            temporary.chmod(0o755)
            temporary.replace(destination)
        finally:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
    destination.chmod(0o755)


def resolve_image_digest(image, digests):
    repository = image.rsplit(":", 1)[0] if ":" in image.rsplit("/", 1)[-1] else image
    digest = next((value for value in (digests or []) if value.startswith(repository + "@sha256:")), None)
    if digest is None:
        raise RuntimeError("Push did not produce a digest for the requested image repository")
    return digest


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
                 "third_party/substrate-sandbox/LICENSE"]:
        target = context / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    for name in ["src", "examples"]:
        target = context / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(ROOT / name, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info", ".env", ".env.*"))
    shutil.copyfile(MANIFESTS / "harness.Dockerfile", context / "Dockerfile")
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
    image = resolve_image_digest(args.image, digests)
    base.configure_resources(args)
    args.state_file.parent.mkdir(parents=True, exist_ok=True)
    args.state_file.write_text(json.dumps(dict(image=image, context=args.context), indent=2) + "\n")
    print("Trusted harness image and Kubernetes resources are ready; no model call was made.")


def run_job(args):
    config = base.configuration(args)
    state = json.loads(args.state_file.read_text())
    if state["context"] != args.context:
        raise ValueError("Harness setup belongs to a different Kubernetes context; run build for this cluster")
    # Require the Secret before creating a pod; never display its data.
    kubectl(args, "get", "secret", args.secret_name, "-n", config.namespace, "-o", "name", capture_output=True)
    job = make_job(state["image"], args.template, args.model, args.secret_name, resources=config)
    apply(args, job)
    name = job["metadata"]["name"]
    print(f"Created {config.namespace}/{name}", flush=True)
    if args.wait:
        wait_for_job(args, name)
    return name


def wait_for_job(args, name):
    config = base.configuration(args)
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        status = json.loads(kubectl(args, "get", "job", name, "-n", config.namespace, "-o", "json", capture_output=True).stdout).get("status", {})
        if status.get("succeeded") or status.get("failed"):
            kubectl(args, "logs", "job/" + name, "-n", config.namespace, "--tail=150")
            if status.get("failed"):
                raise RuntimeError(f"Agent Job {name} failed")
            print(f"Agent Job {name} completed. Artifacts are retained on PVC {config.artifacts_claim}.")
            return
        time.sleep(2)
    raise TimeoutError(f"Timed out waiting for Agent Job {name}")


def collect(args):
    config = base.configuration(args)
    state = json.loads(args.state_file.read_text())
    if state["context"] != args.context:
        raise ValueError("Harness setup belongs to a different Kubernetes context")
    name = "openai-agent-reader-" + uuid.uuid4().hex[:12]
    pod = make_reader_pod(state["image"], name, resources=config)
    try:
        apply(args, pod)
        kubectl(args, "wait", "--for=condition=Ready", "pod/" + name, "-n", config.namespace, "--timeout=120s", capture_output=True)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        kubectl(args, "cp", "-n", config.namespace, name + ":/artifacts/.", str(args.output_dir))
        print(f"Collected agent artifacts into {args.output_dir}")
    finally:
        kubectl(args, "delete", "pod", name, "-n", config.namespace, "--ignore-not-found", capture_output=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["secret", "sandbox", "build", "run", "collect"])
    parser.add_argument("--context", required=True)
    parser.add_argument("--kubeconfig", type=Path)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--secret-name", default=SECRET)
    parser.add_argument("--source-dir", type=Path, default=ROOT / "artifacts/local/substrate")
    parser.add_argument("--image", help="Registry/repository:tag; defaults depend on the selected image build")
    parser.add_argument("--docker-context")
    parser.add_argument("--state-file", type=Path, default=ROOT / "artifacts/local/agent-deployment.json")
    parser.add_argument("--atespace", default="ate-demo-sandbox")
    parser.add_argument("--template", help="Python-capable ActorTemplate; required for run")
    parser.add_argument("--model", help="Override the mounted OPENAI_MODEL; scripted performs no model inference")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--output-dir", type=Path, help="Artifact collection or sandbox deployment output directory")
    base.add_arguments(parser)
    args = parser.parse_args()
    if args.action != "sandbox" and not args.kubeconfig:
        parser.error("this action requires --kubeconfig")
    if args.action == "run" and not args.template:
        parser.error("run requires --template")
    args.output_dir = args.output_dir or (ROOT / "artifacts/examples/document_intelligence/deployment"
                                         if args.action == "sandbox" else ROOT / "artifacts/local/agent-results")
    args.image = args.image or ("localhost:5501/openai-agent-substrate-document-intelligence:local"
                                if args.action == "sandbox" else "localhost:5501/openai-agent-substrate-harness:local")
    {"secret": base.import_secret, "sandbox": sandbox.deploy, "build": build, "run": run_job, "collect": collect}[args.action](args)


if __name__ == "__main__":
    main()
