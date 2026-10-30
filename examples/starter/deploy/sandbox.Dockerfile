# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
# Build with the repository root as the Docker build context.
FROM golang:1.27.3-alpine@sha256:d200d2dd76b03a99b52651fd1325aff7dcd35422c35c01bac56571b0507f3deb AS builder
# Agent Substrate's execution server at the pinned commit; the deploy command passes .substrate-revision.
ARG SUBSTRATE_REVISION
COPY scripts/build-sandbox-server.sh /build-sandbox-server.sh
RUN sh /build-sandbox-server.sh "$SUBSTRATE_REVISION" /out

# The sandbox: Python and a shell, plus the execution server the harness talks to.
# Add the tools your agent needs here (apk add ..., pip install ...).
FROM python:3.14-alpine@sha256:eb2c62658b8d51bff0acc408504b76521d02030de56a8a3c1e830083e89b89d2
COPY --from=builder /out/sandbox-server /usr/local/bin/sandbox-server
COPY --from=builder /out/LICENSE /usr/share/licenses/substrate/LICENSE
ENV PORT=80 PYTHONDONTWRITEBYTECODE=1
EXPOSE 80
ENTRYPOINT ["/usr/local/bin/sandbox-server"]
