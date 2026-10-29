# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Build a Python sandbox image and register an immutable Substrate ActorTemplate."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def checked(command, **kwargs):
    try:
        return subprocess.run(command, check=True, text=True, **kwargs)
    except subprocess.CalledProcessError as error:
        if error.stderr:
            print(error.stderr, end="")
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True)
    parser.add_argument("--kubeconfig", type=Path)
    parser.add_argument("--docker-context", help="Docker daemon used to build and push")
    parser.add_argument("--image", required=True, help="Registry/repository:tag reachable by the sandbox workers")
    parser.add_argument("--atespace", default="ate-demo-sandbox")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/examples/document_intelligence/deployment")
    args = parser.parse_args()
    kubectl = ["kubectl", "ate", "--context", args.context]
    if args.kubeconfig:
        kubectl += ["--kubeconfig", str(args.kubeconfig)]
    # Check the plugin and intended cluster before spending time on the image build.
    existing = json.loads(checked([*kubectl, "get", "actor-templates", "--atespace", args.atespace, "-o", "json"],
                                   capture_output=True).stdout)
    docker = ["docker"] + (["--context", args.docker_context] if args.docker_context else [])
    checked([*docker, "build", "--provenance=false", "-f", str(ROOT / "examples/document_intelligence/agent/sandbox/Dockerfile"),
             "-t", args.image, str(ROOT)])
    checked([*docker, "push", args.image])
    digests = json.loads(checked([*docker, "image", "inspect", args.image, "--format", "{{json .RepoDigests}}"],
                                 capture_output=True).stdout)
    repository = args.image.rsplit(":", 1)[0] if ":" in args.image.rsplit("/", 1)[-1] else args.image
    image = next((d for d in digests if d.startswith(repository + "@sha256:")), None)
    if image is None:
        raise RuntimeError("Push did not produce a digest for the requested image repository")
    template = dict(metadata=dict(atespace=args.atespace),
        workerSelector=dict(matchLabels=dict(workload="sandbox")),
        containers=[dict(name="sandbox", image=image, env=[dict(name="PORT", value="80")])],
        resources=dict(limits=dict(cpuMillicores=2000, memoryBytes=1073741824)),
        snapshotConfig=dict(preferredFidelity="SNAPSHOT_FIDELITY_MEMORY"))
    suffix = hashlib.sha256(json.dumps(template, sort_keys=True).encode()).hexdigest()[:12]
    name = "document-intelligence-" + suffix
    template["metadata"]["name"] = name
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / f"{name}.json"
    manifest_path.write_text(json.dumps(template, indent=2) + "\n")
    if not any(t["metadata"]["name"] == name for t in existing.get("actorTemplates", [])):
        checked([*kubectl, "create", "actor-template", "-f", str(manifest_path)])
    deployment = dict(context=args.context, atespace=args.atespace, template=name, image=image,
                      manifest=str(manifest_path.resolve()))
    (args.output_dir / "deployment.json").write_text(json.dumps(deployment, indent=2) + "\n")
    print(json.dumps(deployment, indent=2))


if __name__ == "__main__":
    main()
