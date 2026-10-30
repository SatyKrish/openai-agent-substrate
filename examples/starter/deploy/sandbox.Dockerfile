# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
# Build with the repository root as the Docker build context.
FROM golang:1.27.3-alpine@sha256:d200d2dd76b03a99b52651fd1325aff7dcd35422c35c01bac56571b0507f3deb AS builder
WORKDIR /src
COPY third_party/substrate-sandbox/main.go ./main.go
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /sandbox-server main.go

# The sandbox: Python and a shell, plus the execution server the harness talks to.
# Add the tools your agent needs here (apk add ..., pip install ...).
FROM python:3.14-alpine@sha256:eb2c62658b8d51bff0acc408504b76521d02030de56a8a3c1e830083e89b89d2
COPY --from=builder /sandbox-server /usr/local/bin/sandbox-server
COPY third_party/substrate-sandbox/LICENSE /usr/share/licenses/substrate/LICENSE
ENV PORT=80 PYTHONDONTWRITEBYTECODE=1
EXPOSE 80
ENTRYPOINT ["/usr/local/bin/sandbox-server"]
