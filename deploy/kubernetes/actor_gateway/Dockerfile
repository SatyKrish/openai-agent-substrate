# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

FROM golang:1.27.3-alpine@sha256:d200d2dd76b03a99b52651fd1325aff7dcd35422c35c01bac56571b0507f3deb AS build
WORKDIR /src
COPY go.mod main.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /actor-gateway .

FROM scratch
COPY --from=build /actor-gateway /actor-gateway
USER 65532:65532
EXPOSE 8443
ENTRYPOINT ["/actor-gateway"]
