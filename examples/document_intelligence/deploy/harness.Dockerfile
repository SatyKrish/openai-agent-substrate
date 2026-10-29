# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
# The example deployment CLI build action prepares an allowlisted context without .env.
FROM ghcr.io/astral-sh/uv:0.11.7@sha256:240fb85ab0f263ef12f492d8476aa3a2e4e1e333f7d67fbdd923d00a506a516a AS uv
FROM python:3.14-alpine@sha256:eb2c62658b8d51bff0acc408504b76521d02030de56a8a3c1e830083e89b89d2
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE NOTICE THIRD_PARTY_NOTICES.md ./
COPY src/ src/
COPY examples/ examples/
COPY third_party/substrate-sandbox/LICENSE third_party/substrate-sandbox/LICENSE
RUN uv sync --frozen --no-dev --no-editable --no-cache
COPY kubectl kubectl-ate /usr/local/bin/
COPY substrate-LICENSE /usr/share/licenses/substrate/LICENSE
ENV PATH=/app/.venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
USER 10001:10001
ENTRYPOINT ["python", "-m", "examples.document_intelligence.deploy.run_agent"]
