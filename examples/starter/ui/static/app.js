// Copyright 2026 openai-agent-substrate contributors
// SPDX-License-Identifier: Apache-2.0
//
// ChatKit on the left; the workspace files of the conversation on the right.
// Everything from the workspace is untrusted and is inserted as text, never as HTML.

const $ = (id) => document.getElementById(id);
const state = { config: {}, chatkit: null, threadId: null, thread: null, path: null, poll: null };

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  node.append(...children.filter((child) => child !== null && child !== undefined));
  return node;
}

function chatkitOptions() {
  const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  return {
    api: { url: "/chatkit", domainKey: "local-dev" },
    initialThread: state.threadId,
    theme: { colorScheme: dark ? "dark" : "light", radius: "round" },
    history: { enabled: true, showDelete: true, showRename: true },
    startScreen: {
      greeting: "Ask the starter agent to compute, write or inspect something in its sandbox",
      prompts: state.config.prompts || [],
    },
    composer: { placeholder: "Ask the agent…", attachments: { enabled: false } },
    threadItemActions: { feedback: false, retry: true },
    widgets: {
      async onAction(action) {
        if (action.type === "open_file") showFile(action.payload.path);
        if (action.type === "open_trace") openTrace(action.payload.turn);
      },
    },
  };
}

// The latest copy of every file the server recorded, across turns.
function files() {
  const latest = new Map();
  for (const turn of state.thread?.turns || []) {
    for (const file of turn.files || []) if (!file.too_large) latest.set(file.path, { ...file, turn: turn.number });
  }
  return [...latest.values()].sort((a, b) => a.path.localeCompare(b.path));
}

async function refresh() {
  if (!state.threadId) {
    state.thread = null;
    return render();
  }
  const response = await fetch(`/api/threads/${encodeURIComponent(state.threadId)}`);
  state.thread = response.ok ? await response.json() : null;
  const running = state.thread?.state === "running";
  if (running && !state.poll) state.poll = setInterval(refresh, 2000);
  if (!running) { clearInterval(state.poll); state.poll = null; }
  render();
}

function render() {
  const thread = state.thread;
  $("status").textContent = !thread?.actor ? "No workspace yet · the first message creates one"
    : `Workspace ${thread.state} · ${thread.actor}`;
  const list = files();
  if (!list.some((file) => file.path === state.path)) state.path = (list.find((f) => f.path.startsWith("output/")) || list[0])?.path || null;
  $("files").replaceChildren(...list.map((file) => el("button", {
    type: "button", "aria-pressed": String(file.path === state.path), onclick: () => showFile(file.path),
  }, file.path)));
  // The latest turn's host check, and a link to its trace in Jaeger.
  const turn = thread?.turns?.at(-1);
  const check = turn?.check;
  const parts = [];
  let kind = "";
  if (turn?.status === "failed") { parts.push(`Turn ${turn.number} failed (${turn.error}).`); kind = "err"; }
  else if (check?.status === "passed") { parts.push("Output files are well formed (host check)."); kind = "ok"; }
  else if (check?.status === "failed") { parts.push(`Host check failed: ${check.detail}`); kind = "err"; }
  if (turn?.otel_trace_id && state.config.jaeger_url) {
    parts.push(" ", el("a", { href: `${state.config.jaeger_url}/trace/${turn.otel_trace_id}`, target: "_blank",
      rel: "noopener noreferrer" }, `Turn ${turn.number} trace`));
  }
  const notice = $("notice");
  notice.replaceChildren(...parts);
  notice.className = `notice ${kind}`;
  notice.hidden = !parts.length || turn?.status === "running";
  const viewer = $("viewer");
  if (state.path) loadFile(list.find((file) => file.path === state.path));
  else if (viewer.dataset.key !== "") {
    viewer.dataset.key = "";
    viewer.replaceChildren(el("p", { class: "muted" }, "The files the agent writes in output/ and work/ appear here after each turn."));
  }
}

async function loadFile(file) {
  const url = `/api/threads/${encodeURIComponent(state.threadId)}/turns/${file.turn}/files/${file.path.split("/").map(encodeURIComponent).join("/")}`;
  const key = `${state.threadId}:${file.turn}:${file.path}`;
  const viewer = $("viewer");
  if (viewer.dataset.key === key) return;
  viewer.dataset.key = key;
  const response = await fetch(url);
  const text = response.ok ? await response.text() : "The file could not be loaded.";
  if (viewer.dataset.key === key) viewer.replaceChildren(el("pre", {}, text));
}

function showFile(path) {
  state.path = path;
  render();
}

function openTrace(turnNumber) {
  const turn = state.thread?.turns?.find((item) => item.number === turnNumber);
  if (turn?.otel_trace_id && state.config.jaeger_url) window.open(`${state.config.jaeger_url}/trace/${turn.otel_trace_id}`, "_blank", "noopener");
}

async function main() {
  state.config = await (await fetch("/api/config")).json();
  $("model").textContent = state.config.model;
  $("new-chat").addEventListener("click", () => state.chatkit?.setThreadId(null));
  try {
    await Promise.race([customElements.whenDefined("openai-chatkit"),
      new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), 15000))]);
  } catch {
    $("chat-error").textContent = "ChatKit did not load from cdn.platform.openai.com. Check your network, then reload.";
    $("chat-error").hidden = false;
    return;
  }
  const chatkit = document.createElement("openai-chatkit");
  state.chatkit = chatkit;
  $("chat").append(chatkit);
  chatkit.addEventListener("chatkit.thread.change", (event) => {
    state.threadId = event.detail.threadId || null;
    state.path = null;
    $("viewer").dataset.key = "stale";
    refresh();
  });
  chatkit.addEventListener("chatkit.response.start", () => { if (!state.poll) state.poll = setInterval(refresh, 2000); });
  chatkit.addEventListener("chatkit.response.end", refresh);
  chatkit.addEventListener("chatkit.effect", (event) => {
    if (event.detail?.name === "open_file") refresh().then(() => showFile(event.detail.data.path));
  });
  chatkit.setOptions(chatkitOptions());
}

main();
