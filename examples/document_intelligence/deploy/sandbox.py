# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Build the Actor image and register an immutable Document Intelligence template."""

import hashlib
import json
from pathlib import Path

from deploy.kubernetes.base.resources import checked

from ..agent.sandbox.sandbox import ACTOR_WORKSPACE

ROOT = Path(__file__).resolve().parents[3]


def build_template(image, atespace):
    """The immutable ActorTemplate; its name is a hash of its content."""
    template = dict(metadata=dict(atespace=atespace),
        workerSelector=dict(matchLabels=dict(workload="sandbox")),
        containers=[dict(name="sandbox", image=image, env=[dict(name="PORT", value="80")],
                         volumeMounts=[dict(name="workspace", mountPath=ACTOR_WORKSPACE)])],
        # A per-Actor durableDir: created with the Actor, kept across suspend and resume
        # by every snapshot, and deleted with the Actor.
        volumes=[dict(name="workspace", durableDir={})],
        # Half a core and 480 MiB: four Actors fit a local Kind worker (2 cores, 1,984 MiB reported).
        # Turns mostly wait on the model; PDF processing peaks near 22 MiB.
        resources=dict(limits=dict(cpuMillicores=500, memoryBytes=480 * 1024 * 1024)),
        snapshotConfig=dict(preferredFidelity="SNAPSHOT_FIDELITY_MEMORY"))
    suffix = hashlib.sha256(json.dumps(template, sort_keys=True).encode()).hexdigest()[:12]
    template["metadata"]["name"] = "document-intelligence-" + suffix
    return template


def deploy(args):
    kubectl = ["kubectl", "ate", "--context", args.context]
    if args.kubeconfig:
        kubectl += ["--kubeconfig", str(args.kubeconfig)]
    # Check the plugin and intended cluster before spending time on the image build.
    existing = json.loads(checked([*kubectl, "get", "actor-templates", "--atespace", args.atespace, "-o", "json"],
                                   capture_output=True).stdout)
    docker = ["docker"] + (["--context", args.docker_context] if args.docker_context else [])
    checked([*docker, "build", "--provenance=false", "-f", str(ROOT / "examples/document_intelligence/deploy/sandbox.Dockerfile"),
             "-t", args.image, str(ROOT)])
    checked([*docker, "push", args.image])
    digests = json.loads(checked([*docker, "image", "inspect", args.image, "--format", "{{json .RepoDigests}}"],
                                 capture_output=True).stdout)
    repository = args.image.rsplit(":", 1)[0] if ":" in args.image.rsplit("/", 1)[-1] else args.image
    image = next((d for d in digests if d.startswith(repository + "@sha256:")), None)
    if image is None:
        raise RuntimeError("Push did not produce a digest for the requested image repository")
    template = build_template(image, args.atespace)
    name = template["metadata"]["name"]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / f"{name}.json"
    manifest_path.write_text(json.dumps(template, indent=2) + "\n")
    if not any(t["metadata"]["name"] == name for t in existing.get("actorTemplates", [])):
        checked([*kubectl, "create", "actor-template", "-f", str(manifest_path)])
    deployment = dict(context=args.context, atespace=args.atespace, template=name, image=image,
                      manifest=str(manifest_path.resolve()))
    (args.output_dir / "deployment.json").write_text(json.dumps(deployment, indent=2) + "\n")
    print(json.dumps(deployment, indent=2))
