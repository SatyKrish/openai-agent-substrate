# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Build the Document Intelligence Job and artifact reader without templates."""

import uuid

from deploy.kubernetes.base.resources import SUBSTRATE_MOUNT, ApplicationResources, substrate_volume

SECRET = "openai-agent-env"


def make_job(image, template, model=None, secret_name=SECRET, *, name=None, resources=None):
    resources = resources or ApplicationResources()
    container = {
        "name": "agent", "image": image, "imagePullPolicy": "IfNotPresent",
        "envFrom": [{"configMapRef": {"name": "openai-agent-tracing", "optional": True}}, {"secretRef": {"name": secret_name}}],
        # The atespace where configure_resources granted this ServiceAccount access.
        "env": [{"name": "SUBSTRATE_TEMPLATE", "value": template}, {"name": "SUBSTRATE_ATESPACE", "value": resources.atespace}],
        "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}},
        "resources": {"requests": {"cpu": "100m", "memory": "256Mi"}, "limits": {"cpu": "1", "memory": "1Gi"}},
        "volumeMounts": [
            {"name": "agent-config", "mountPath": "/var/run/agent-config", "readOnly": True},
            {"name": "kubeconfig", "mountPath": "/etc/agent", "readOnly": True},
            {"name": "substrate", "mountPath": SUBSTRATE_MOUNT, "readOnly": True},
            {"name": "artifacts", "mountPath": "/artifacts"}, {"name": "tmp", "mountPath": "/tmp"},
        ],
    }
    if model == "scripted":
        container["args"] = ["--test"]
    elif model:
        container["args"] = ["--model", model]

    def labels():
        return {"app.kubernetes.io/name": "openai-agent-substrate", "app.kubernetes.io/component": "harness"}

    return {"apiVersion": "batch/v1", "kind": "Job",
            "metadata": {"name": name or "openai-agent-" + uuid.uuid4().hex[:12], "namespace": resources.namespace, "labels": labels()},
            "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 1800, "ttlSecondsAfterFinished": 86400,
                     "template": {"metadata": {"labels": labels()}, "spec": {
                         "serviceAccountName": resources.service_account, "restartPolicy": "Never",
                         "securityContext": {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001,
                                             "fsGroup": 10001, "seccompProfile": {"type": "RuntimeDefault"}},
                         "containers": [container],
                         "volumes": [
                             {"name": "agent-config", "secret": {"secretName": secret_name, "defaultMode": 288}},
                             {"name": "kubeconfig", "configMap": {"name": resources.kubeconfig_name}},
                             substrate_volume(),
                             {"name": "artifacts", "persistentVolumeClaim": {"claimName": resources.artifacts_claim}},
                             {"name": "tmp", "emptyDir": {}},
                         ],
                     }}}}


def make_reader_pod(image, name, *, resources=None):
    resources = resources or ApplicationResources()
    return dict(apiVersion="v1", kind="Pod", metadata=dict(name=name, namespace=resources.namespace), spec=dict(
        automountServiceAccountToken=False, restartPolicy="Never",
        securityContext=dict(runAsNonRoot=True, runAsUser=10001, runAsGroup=10001, fsGroup=10001, seccompProfile=dict(type="RuntimeDefault")),
        containers=[dict(name="reader", image=image, command=["python", "-c", "import time; time.sleep(600)"],
            securityContext=dict(allowPrivilegeEscalation=False, readOnlyRootFilesystem=True, capabilities=dict(drop=["ALL"])),
            volumeMounts=[dict(name="artifacts", mountPath="/artifacts", readOnly=True)])],
        volumes=[dict(name="artifacts", persistentVolumeClaim=dict(claimName=resources.artifacts_claim, readOnly=True))]))
