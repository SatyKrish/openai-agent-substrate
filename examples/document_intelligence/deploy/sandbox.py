# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Build the Actor image and register an immutable Document Intelligence template."""

from pathlib import Path

from deploy.kubernetes import sandbox as profiles

PROFILE = profiles.SandboxProfile("document-intelligence", Path(__file__).resolve().parent / "sandbox.Dockerfile")


def build_template(image, atespace):
    """The immutable ActorTemplate; its name is a hash of its content."""
    return profiles.build_template(PROFILE, image, atespace)


def deploy(args):
    return profiles.deploy(PROFILE, args)
