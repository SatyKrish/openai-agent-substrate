# Contributing

Thank you for contributing to openai-agent-substrate. This project demonstrates Kubernetes-backed sandbox execution for the OpenAI Agents SDK through Agent Substrate.

## Get started

Fork the repository, clone your fork, and create a branch for your change. Use Python 3.11+ on macOS or Linux:

```sh
uv sync --locked
uv run pytest -q
uv run python -m examples.quickstart --backend local
```

The default tests and scripted demo use loopback HTTP and temporary local workspaces. They require no API key or cluster. Detailed checks, including the actual upstream execution server, are in the [development guide](docs/development.md).

## Propose a change

Use an issue to discuss substantial API or architecture changes before implementation. For bugs, include the SDK and Python versions, a minimal reproduction, and expected versus observed behavior. Follow [SECURITY.md](SECURITY.md) for vulnerabilities.

Keep changes focused, add tests for behavior changes, and update the relevant guide when interfaces or limits change. Preserve saved-session compatibility and keep unsupported features explicit. Do not change vendored upstream files without updating their provenance and notices.

## Open a pull request

Explain the problem, resulting behavior, and how you verified the change. The CI workflow checks supported Python versions, the SDK/provider tests, the scripted demo, upstream execution-server compatibility, and package builds.

Before submitting packaging changes, run:

```sh
uv build --no-sources
uv run --locked twine check dist/*
```

## Collaboration and licensing

Be respectful, assume good intent, and focus feedback on the work. Harassment and discrimination are not acceptable; maintainers may remove disruptive content or restrict participation.

By intentionally submitting a contribution for inclusion, you agree to license it under the project's Apache-2.0 license unless explicitly stated otherwise. Keep attribution for any third-party material you include.
