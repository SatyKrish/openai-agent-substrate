# Domain examples

Each folder contains an agent project with `agent/` and sibling `evals/`, following
[Eve’s organization](https://eve.dev/docs/concepts/project-structure). It contains
an end-to-end domain workflow: inputs, agent, reusable
skills, sandbox runtime requirements, and verified output artifacts. Run commands
from the repository root after `uv sync --locked`.

| Domain | Workflow | Sandbox capabilities |
| --- | --- | --- |
| [Document Intelligence](document_intelligence/README.md) | Compare synthetic annual reports, reconcile source facts, calculate financial metrics, and create cited reports | Native SDK skill discovery, shell inspection, agent-written Python via `apply_patch`, code execution, artifact verification |

```text
examples/
  document_intelligence/
    agent/
      agent.py
      instructions.md
      tools/
      skills/document-analysis/
      lib/
      sandbox/
        sandbox.py
        Dockerfile
        workspace/inputs/
    evals/
      scripted.py
      fixtures/
      test_workflow.py
      test_configuration.py
    main.py
    deploy.py
    README.md
```

Generic infrastructure utilities belong in `scripts/`. Use
`scripts/resume_session.py` to inspect a retained Actor; see the
[session guide](../docs/usage.md). The provider's small lifecycle smoke test
remains available as `uv run openai-agent-substrate --backend local`.
