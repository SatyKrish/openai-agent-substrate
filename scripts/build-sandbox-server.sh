#!/bin/sh
# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
#
# Build Agent Substrate's execution server, the program that runs in every sandbox Actor.
# Usage: build-sandbox-server.sh REVISION OUTDIR
#
# The source is upstream's demos/sandbox package at REVISION (the commit in .substrate-revision),
# downloaded through the Go module proxy and checked against the Go checksum database. It imports
# only the standard library, so it builds on its own; `go install pkg@version` would refuse because
# upstream's go.mod has a replace directive. Writes OUTDIR/sandbox-server and upstream's LICENSE.
set -eu

revision="$1"
out="$2"
case "$revision" in
  *[!0-9a-f]* | "") echo "REVISION must be a commit hash" >&2; exit 1 ;;
esac
module="$(go mod download -json "go.ate.dev/substrate@${revision}" | grep '"Dir"' | cut -d '"' -f 4)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cp "$module/demos/sandbox/main.go" "$work/main.go"
mkdir -p "$out"
out="$(cd "$out" && pwd)"  # absolute: the build runs in a temporary folder
rm -f "$out/LICENSE"
cp "$module/LICENSE" "$out/LICENSE"
chmod 644 "$out/LICENSE"  # the module cache is read-only
cd "$work"
go mod init sandbox-server >/dev/null 2>&1
CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o "$out/sandbox-server" .
