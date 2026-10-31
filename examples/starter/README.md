# Starter agent

The smallest complete agent on the [shared harness](../../harness/README.md): a
general workspace assistant that writes and runs Python in its own sandbox and
saves results in `output/`. Copy this folder to build your own agent.

## Run it

After the [quick start](../../README.md#quick-start)'s cluster setup and `.env`:

```sh
./scripts/local-kubernetes.sh starter-setup   # sandbox image and ActorTemplate
./scripts/local-kubernetes.sh starter-chat    # chat in the terminal
./scripts/local-kubernetes.sh starter-ui      # optional: the same conversations at http://localhost:8002
```

Each conversation gets its own Agent Substrate Actor: the first message creates
it, later messages resume it, and deleting the conversation (in the UI) deletes
it. The model is live (`OPENAI_MODEL` and `OPENAI_API_KEY` from `.env`).

In the terminal chat, `/list` shows recent conversations, `/open ID` continues
one, `/files` shows where this conversation's files are on your machine, `/new`
starts another and `/quit` exits; the Actor stays suspended. Pass a question as
an argument to ask once and exit, and `--thread ID` to continue a conversation.
The chat and the UI read the same data folder, so each sees the other's
conversations; use one of them for a conversation at a time.

## The four pieces

```text
agent/
  agent.py          The SandboxAgent: model, instructions, capabilities (shell, filesystem)
  instructions.md   What the agent does
  spec.py           AgentSpec: the starting workspace and the host check
  check.py          The host check: runs after each turn on copies of output/
deploy/
  sandbox.Dockerfile  The sandbox image: Python, a shell, and the execution server
  __main__.py         Its profile (name, CPU, memory) and the `sandbox` deploy action
__main__.py         The terminal chat (starter-chat), on the harness's TerminalChat
server.py           Settings and the harness server, shared by the chat and the UI
card.py             The turn card's check rows and each file's check status, shown by both
ui/
  __main__.py       The optional UI's launcher (starter-ui)
  app.py            This agent's settings for the harness's shared page: title, prompts, follow-ups
evals/              A turn end to end with a scripted model, and the check
```

## Make it yours

1. Copy the folder (for example to `examples/my_agent`) and rename `starter` in
   `spec.py` (`name`), `deploy/__main__.py` (profile and image names),
   `server.py` (deployment and data folders) and the UI's port and title in
   `ui/`. The UI is the harness's shared page, configured by `ui/app.py`; it is
   optional: delete `ui/` if the terminal chat is enough. To show a file type
   the page does not render, add `ui/static/agent.js` (Document Intelligence's
   renders PDF pages).
2. **Agent:** edit `instructions.md`; add SDK capabilities such as `Skills` in
   `agent.py` (see the Document Intelligence agent for a skill).
3. **Workspace and check:** stage input files in `spec.py`'s `start`, and check
   what the agent wrote in `check.py` against something you trust.
4. **Sandbox:** add the tools the agent needs to `deploy/sandbox.Dockerfile`, and
   raise the CPU or memory in the profile if it needs more than 0.5 CPU and
   480 MiB.
5. **Harness tools** (optional): tools that need credentials, such as reading an
   API, run in the harness instead of the sandbox. Return them from
   `AgentSpec(tools=...)`; the [SRE agent](../sre/README.md) shows how, and how
   to record their output as evidence for the sandbox and the check.
6. **Uploads** (optional): pass `AgentSpec(uploads=Uploads(...))`, as Document
   Intelligence does.
7. Add `my-agent-setup` and `my-agent-chat` (and `my-agent-ui`) commands to
   `scripts/local-kubernetes.sh`, modeled on the starter's.

Run its tests with `uv run --locked --group ui pytest -q examples/starter/evals`.
