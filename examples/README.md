# Domain examples

Each folder contains an end-to-end domain workflow: inputs, agent, reusable
skills, sandbox runtime requirements, and verified output artifacts. Run commands
from the repository root after `uv sync --locked`.

| Domain | Workflow | Sandbox capabilities |
| --- | --- | --- |
| [Document Intelligence](document_intelligence/README.md) | Compare synthetic annual reports, reconcile source facts, calculate financial metrics, and create cited reports | Native SDK skill discovery, shell inspection, agent-written Python via `apply_patch`, code execution, artifact verification |

```text
examples/
  document_intelligence/
    main.py                  SDK harness and trusted artifact checks
    scripted.py              Key-free tool decisions for execution testing
    scripted_analysis.py     Code fixture emitted by the scripted model
    verify.py                Host-side source reconciliation
    fixtures/                MIT-licensed synthetic reports and KPI table
    skills/document-analysis/
      SKILL.md
      scripts/documents.py   Sandbox extraction and verification
      references/            Output contract
    Dockerfile               Python-capable Actor image
    deploy.py                Image build, push, and immutable ActorTemplate
    README.md
```

Generic infrastructure utilities belong in `scripts/`. Use
`scripts/resume_session.py` to inspect a retained Actor; see the
[session guide](../docs/usage.md). The provider's small lifecycle smoke test
remains available as `uv run openai-agent-substrate`.
