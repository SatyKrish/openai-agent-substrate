# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Document Intelligence agent Job and artifact reader manifests."""

import json
from pathlib import Path
import uuid

from deploy.kubernetes.base.resources import NAMESPACE

MANIFESTS = Path(__file__).parent
SECRET = "openai-agent-env"


def make_job(image, template, model=None, secret_name=SECRET):
    job = json.loads((MANIFESTS / "agent-job.json").read_text())
    job["metadata"]["name"] = "openai-agent-" + uuid.uuid4().hex[:12]
    container = job["spec"]["template"]["spec"]["containers"][0]
    container["image"] = image
    container["env"][0]["value"] = template
    next(item["secretRef"] for item in container["envFrom"] if "secretRef" in item)["name"] = secret_name
    job["spec"]["template"]["spec"]["volumes"][0]["secret"]["secretName"] = secret_name
    if model == "scripted":
        container["args"] = ["--test"]
    elif model:
        container["args"] = ["--model", model]
    return job


def make_reader_pod(image, name):
    return dict(apiVersion="v1", kind="Pod", metadata=dict(name=name, namespace=NAMESPACE), spec=dict(
        automountServiceAccountToken=False, restartPolicy="Never",
        securityContext=dict(runAsNonRoot=True, runAsUser=10001, runAsGroup=10001, fsGroup=10001, seccompProfile=dict(type="RuntimeDefault")),
        containers=[dict(name="reader", image=image, command=["python", "-c", "import time; time.sleep(600)"],
            securityContext=dict(allowPrivilegeEscalation=False, readOnlyRootFilesystem=True, capabilities=dict(drop=["ALL"])),
            volumeMounts=[dict(name="artifacts", mountPath="/artifacts", readOnly=True)])],
        volumes=[dict(name="artifacts", persistentVolumeClaim=dict(claimName="openai-agent-artifacts", readOnly=True))]))
