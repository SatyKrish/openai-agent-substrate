# Execution evaluations

This directory contains the deterministic scripted model, its analysis-code
fixture, and document-workflow contract checks. Run from the repository root:

```sh
uv sync --locked --group ui
uv run pytest -q examples/document_intelligence/evals
uv run python -m examples.document_intelligence --test
```

`test_ui.py` and `test_pdf.py` drive the agent through the shared harness and
are skipped without the `ui` group.

These checks run the actual SDK tools and sandbox execution with fixed model
decisions. They do not call OpenAI or measure live-model reasoning. Live execution
uses the model selected by OPENAI_MODEL or --model and the configured OpenAI
endpoint. It requires a real API key.

The agent definition under ../agent/ accepts an SDK model and has no dependency
on this scripted model. Only the explicitly selected evaluation path imports it.
