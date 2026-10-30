# Examples

Run examples from the repository root after `uv sync --locked`. They are source
applications and are not installed by the library wheel.

| Example | Workflow |
| --- | --- |
| [Quickstart](quickstart/README.md) | Provider execution, suspension, session serialization and resume |
| [Document Intelligence](document_intelligence/README.md) | Skill loading, source reconciliation, agent-written Python, sandbox execution and cited reports |
| [Document Intelligence UI](document_intelligence/ui/README.md) | ChatKit conversations, one Actor and workspace volume each, with uploads, per-turn files and Jaeger traces |

Document Intelligence keeps authored behavior in `agent/`, evaluations in
`evals/`, application deployment in `deploy/`, and its ChatKit UI in `ui/`. It follows
[Eve’s organization](https://eve.dev/docs/concepts/project-structure).
Reusable Kubernetes permissions and connection settings live in
[the base deployment](../deploy/kubernetes/base/README.md). `scripts/` contains
only the shell bootstrap and dispatches to the Python module CLIs.

Use `python -m examples.quickstart.resume` to inspect a retained Actor. See the
[session guide](../docs/usage.md) for state handling.
