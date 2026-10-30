# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""An agent's sandbox profile, built as an image and registered as an immutable ActorTemplate.

Deploy-time code: the harness runtime never imports it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .base.resources import checked

ROOT = Path(__file__).resolve().parents[2]
# The ActorTemplate mounts a per-Actor durableDir volume here, so the workspace lives as long as the Actor.
# The harness uses the same root (harness.spec.WORKSPACE_ROOT).
WORKSPACE_ROOT = "/workspace"
MIB = 1024 * 1024


@dataclass(frozen=True)
class SandboxProfile:
    """The image and size an agent's sandboxes run with. Half a core and 480 MiB fit four Actors on a
    local Kind worker (2 cores, 1,984 MiB reported); turns mostly wait on the model."""

    name: str
    dockerfile: Path
    cpu_millicores: int = 500
    memory_bytes: int = 480 * MIB


def build_template(profile: SandboxProfile, image: str, atespace: str) -> dict:
    """The immutable ActorTemplate; its name is a hash of its content."""
    template = dict(metadata=dict(atespace=atespace),
        workerSelector=dict(matchLabels=dict(workload="sandbox")),
        containers=[dict(name="sandbox", image=image, env=[dict(name="PORT", value="80")],
                         volumeMounts=[dict(name="workspace", mountPath=WORKSPACE_ROOT)])],
        # A per-Actor durableDir: created with the Actor, kept across suspend and resume
        # by every snapshot, and deleted with the Actor.
        volumes=[dict(name="workspace", durableDir={})],
        resources=dict(limits=dict(cpuMillicores=profile.cpu_millicores, memoryBytes=profile.memory_bytes)),
        snapshotConfig=dict(preferredFidelity="SNAPSHOT_FIDELITY_MEMORY"))
    suffix = hashlib.sha256(json.dumps(template, sort_keys=True).encode()).hexdigest()[:12]
    template["metadata"]["name"] = f"{profile.name}-{suffix}"
    return template


def deploy(profile: SandboxProfile, args) -> dict:
    """Build and push the image, register its template if new, and record it in ``args.output_dir``."""
    kubectl = ["kubectl", "ate", "--context", args.context]
    if args.kubeconfig:
        kubectl += ["--kubeconfig", str(args.kubeconfig)]
    # Check the plugin and intended cluster before spending time on the image build.
    existing = json.loads(checked([*kubectl, "get", "actor-templates", "--atespace", args.atespace, "-o", "json"],
                                   capture_output=True).stdout)
    docker = ["docker"] + (["--context", args.docker_context] if args.docker_context else [])
    # The image builds Agent Substrate's execution server at the same commit as the cluster.
    revision = (ROOT / ".substrate-revision").read_text().strip()
    checked([*docker, "build", "--provenance=false", "--build-arg", f"SUBSTRATE_REVISION={revision}",
             "-f", str(profile.dockerfile), "-t", args.image, str(ROOT)])
    checked([*docker, "push", args.image])
    digests = json.loads(checked([*docker, "image", "inspect", args.image, "--format", "{{json .RepoDigests}}"],
                                 capture_output=True).stdout)
    repository = args.image.rsplit(":", 1)[0] if ":" in args.image.rsplit("/", 1)[-1] else args.image
    image = next((d for d in digests if d.startswith(repository + "@sha256:")), None)
    if image is None:
        raise RuntimeError("Push did not produce a digest for the requested image repository")
    template = build_template(profile, image, args.atespace)
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
    return deployment


def deployed_template(deployment_dir: Path, context: str) -> str | None:
    """The template from the last sandbox deployment to this context, if any."""
    path = Path(deployment_dir) / "deployment.json"
    if not path.is_file():
        return None
    deployment = json.loads(path.read_text())
    return deployment["template"] if deployment.get("context") == context else None
