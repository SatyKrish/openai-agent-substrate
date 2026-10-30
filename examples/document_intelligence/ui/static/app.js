// Copyright 2026 openai-agent-substrate contributors
// SPDX-License-Identifier: Apache-2.0
//
// ChatKit on the left; the artifact panel on the right shows each turn's files and
// its Jaeger trace. Every value from the workspace is untrusted and is inserted as
// text, never as HTML.

const $ = (id) => document.getElementById(id);
const media = window.matchMedia("(prefers-color-scheme: dark)");
const state = {
  config: null,
  chatkit: null,
  threadId: null,
  thread: null,
  turn: null,
  view: null, // { kind: "file", path } or { kind: "trace" }
  poll: null,
  themeChoice: readStorage("di-theme") || "system",
};

function readStorage(key) {
  try { return window.localStorage.getItem(key); } catch { return null; }
}
function writeStorage(key, value) {
  try { value === null ? window.localStorage.removeItem(key) : window.localStorage.setItem(key, value); } catch { /* private mode */ }
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "style") node.style.cssText = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

async function getJSON(url) {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  const body = await response.json().catch(() => ({}));
  return { status: response.status, body };
}

/* ---------- Theme ---------- */

function resolvedScheme() {
  return state.themeChoice === "system" ? (media.matches ? "dark" : "light") : state.themeChoice;
}

function applyTheme() {
  const scheme = resolvedScheme();
  document.documentElement.dataset.theme = scheme;
  for (const button of document.querySelectorAll("[data-theme-choice]")) {
    button.setAttribute("aria-pressed", String(button.dataset.themeChoice === state.themeChoice));
  }
  if (state.chatkit) state.chatkit.setOptions(chatkitOptions());
}

/* ---------- ChatKit ---------- */

function chatkitOptions() {
  const scheme = resolvedScheme();
  return {
    // two_phase: attachments.create returns our upload URL, then the browser PUTs the file there.
    api: { url: "/chatkit", domainKey: "local-dev", uploadStrategy: { type: "two_phase" } },
    initialThread: state.threadId,
    theme: {
      colorScheme: scheme,
      radius: "round",
      density: "normal",
      color: { accent: { primary: scheme === "dark" ? "#97ADE9" : "#2F5BD3", level: 1 } },
    },
    header: { enabled: true, title: { enabled: true } },
    history: { enabled: true, showDelete: true, showRename: true },
    startScreen: {
      greeting: "Compare the synthetic annual reports in an isolated workspace",
      prompts: (state.config?.prompts || []).map((prompt) => ({ label: prompt.label, prompt: prompt.prompt })),
    },
    composer: {
      placeholder: "Ask about the reports, or attach your own .md, .txt or .csv files…",
      attachments: {
        enabled: true,
        maxCount: 5,
        maxSize: 1024 * 1024,
        accept: { "text/markdown": [".md"], "text/plain": [".txt"], "text/csv": [".csv"] },
      },
    },
    threadItemActions: { feedback: false, retry: true },
    widgets: {
      async onAction(action) {
        const payload = action.payload || {};
        if (action.type === "open_file") openView(payload.turn, { kind: "file", path: payload.path });
        if (action.type === "open_trace") openView(payload.turn, { kind: "trace" });
      },
    },
  };
}

async function startChat() {
  try {
    await Promise.race([
      customElements.whenDefined("openai-chatkit"),
      new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), 15000)),
    ]);
  } catch {
    const message = $("chat-error");
    message.textContent = "ChatKit did not load from cdn.platform.openai.com. Check your network, then reload.";
    message.hidden = false;
    return;
  }
  const chatkit = document.createElement("openai-chatkit");
  state.chatkit = chatkit;
  $("chat-host").append(chatkit);
  chatkit.addEventListener("chatkit.thread.change", (event) => selectThread(event.detail.threadId));
  chatkit.addEventListener("chatkit.response.start", () => startPolling());
  chatkit.addEventListener("chatkit.response.end", () => refreshThread({ followLatest: true }));
  chatkit.addEventListener("chatkit.effect", (event) => {
    const { name, data } = event.detail || {};
    if (name === "open_file" && data && data.thread_id === state.threadId) {
      refreshThread({ followLatest: true }).then(() => openView(data.turn, { kind: "file", path: data.path }));
    }
  });
  chatkit.addEventListener("chatkit.error", (event) => console.warn("ChatKit error", event.detail?.error));
  chatkit.setOptions(chatkitOptions());
}

/* ---------- Thread state ---------- */

function selectThread(threadId) {
  state.threadId = threadId || null;
  writeStorage("di-thread", state.threadId);
  state.thread = null;
  state.turn = null;
  state.view = null;
  if (!threadId) {
    stopPolling();
    render();
    return;
  }
  refreshThread({ followLatest: true });
}

async function refreshThread({ followLatest = false } = {}) {
  if (!state.threadId) return render();
  const { status, body } = await getJSON(`/api/threads/${encodeURIComponent(state.threadId)}`);
  if (status !== 200) {
    state.thread = null;
    return render();
  }
  const previousTurns = state.thread?.turns?.length || 0;
  state.thread = body;
  const turns = body.turns || [];
  if (turns.length && (followLatest || state.turn === null || turns.length !== previousTurns)) {
    state.turn = turns[turns.length - 1].number;
    if (!state.view || followLatest) state.view = defaultView(currentTurn());
  }
  if (body.state === "running") startPolling(); else stopPolling();
  render();
}

function startPolling() {
  if (state.poll) return;
  state.poll = setInterval(() => refreshThread(), 2000);
}
function stopPolling() {
  clearInterval(state.poll);
  state.poll = null;
}

function currentTurn() {
  return state.thread?.turns?.find((turn) => turn.number === state.turn) || null;
}

function filesUpTo(turnNumber) {
  // Each turn's tabs show the latest copy of every file collected so far.
  const latest = new Map();
  for (const turn of state.thread?.turns || []) {
    if (turn.number > turnNumber) break;
    for (const file of turn.files || []) latest.set(file.path, { ...file, turn: turn.number });
  }
  const order = (path) => (path.startsWith("output/") ? 0 : path.startsWith("inputs/") ? 1 : 2);
  return [...latest.values()].sort((a, b) => order(a.path) - order(b.path) || a.path.localeCompare(b.path));
}

function defaultView(turn) {
  if (!turn) return null;
  const changed = (turn.files || []).filter((file) => file.changed && file.path.startsWith("output/"));
  const report = changed.find((file) => file.path === "output/comparison.md") || changed[0];
  if (report) return { kind: "file", path: report.path };
  if (turn.otel_trace_id) return { kind: "trace" };
  const files = filesUpTo(turn.number);
  return files.length ? { kind: "file", path: files[0].path } : null;
}

function openView(turnNumber, view) {
  if (turnNumber) state.turn = Number(turnNumber);
  state.view = view;
  render();
}

/* ---------- Rendering ---------- */

function render() {
  renderHeader();
  renderStatus();
  renderPanel();
}

function renderHeader() {
  $("thread-title").textContent = state.thread?.title || "New analysis";
}

function renderStatus() {
  const dot = $("workspace-dot");
  const text = $("workspace-text");
  const thread = state.thread;
  dot.classList.toggle("running", thread?.state === "running");
  if (!thread || !thread.actor) {
    text.textContent = "No workspace yet · the first message creates an Actor";
  } else if (thread.state === "running") {
    text.textContent = `Workspace running · ${thread.actor} · suspends when the turn ends`;
  } else {
    const resumed = (thread.turns || []).filter((turn) => turn.lifecycle === "resumed").length;
    text.textContent = `Workspace suspended · ${thread.actor} · created once, resumed ${resumed} time${resumed === 1 ? "" : "s"} · resumes on your next message`;
  }
}

function renderPanel() {
  const turn = currentTurn();
  const select = $("turn-select");
  const tabs = $("tabs");
  const body = $("artifact-body");
  const download = $("download-link");
  select.replaceChildren(...(state.thread?.turns || []).map((item) =>
    el("option", { value: item.number, selected: item.number === state.turn },
      `Turn ${item.number}${item.status === "running" ? " · running" : item.status === "failed" ? " · failed" : ""}`)));
  select.disabled = !(state.thread?.turns?.length > 1);
  select.hidden = !state.thread?.turns?.length;
  tabs.replaceChildren();
  body.className = "artifact-body";
  download.hidden = true;

  if (!turn) {
    $("artifact-title").textContent = "Workspace files";
    $("artifact-sub").textContent = "Outputs and traces for each turn appear here";
    body.replaceChildren(el("div", { class: "empty" },
      el("h3", {}, "Ask a question to start"),
      el("p", {}, "Each conversation gets its own Agent Substrate Actor with its own workspace volume. Start from the sample " +
        "reports, or attach your own .md, .txt or .csv files: they are copied into the volume, which lives exactly as long " +
        "as the conversation. After every turn the server copies the workspace files here, checks the results against the " +
        "original documents, and suspends the Actor until your next message."),
      el("p", {}, state.config?.tracing ? "Each turn also has a trace in Jaeger." : "Tracing is off: set OTEL_EXPORTER_OTLP_ENDPOINT to see traces.")));
    return;
  }

  const files = filesUpTo(turn.number);
  const view = state.view || defaultView(turn);
  for (const file of files) {
    const changedHere = file.turn === turn.number && file.changed;
    tabs.append(el("button", {
      type: "button", "aria-pressed": String(view?.kind === "file" && view.path === file.path),
      onclick: () => openView(turn.number, { kind: "file", path: file.path }),
    }, file.path.replace(/^output\//, "").replace(/^inputs\//, ""),
      changedHere ? el("span", { class: "badge" }, file.upload ? "uploaded" : file.new ? "new" : "changed") : null));
  }
  if (turn.otel_trace_id) {
    tabs.append(el("button", {
      type: "button", "aria-pressed": String(view?.kind === "trace"),
      onclick: () => openView(turn.number, { kind: "trace" }),
    }, "Trace"));
  }

  if (!view) {
    $("artifact-title").textContent = `Turn ${turn.number}`;
    $("artifact-sub").textContent = turn.status === "running" ? "The agent is working…" : "This turn wrote no files";
    body.replaceChildren(turnNotice(turn) || el("div", { class: "empty" }, el("p", {}, "No files yet.")));
    return;
  }
  if (view.kind === "trace") return renderTrace(turn, body);
  const file = files.find((item) => item.path === view.path);
  if (!file) {
    body.replaceChildren(el("div", { class: "empty" }, el("p", {}, `${view.path} does not exist in turn ${turn.number}.`)));
    return;
  }
  renderFile(turn, file, body, download);
}

function turnNotice(turn) {
  const check = turn.check;
  if (turn.status === "failed") return el("div", { class: "notice err" }, `Turn ${turn.number} failed (${turn.error}). The workspace was suspended.`);
  if (!check) return null;
  if (check.status === "passed") return el("div", { class: "notice ok" }, "comparison.md, kpis.csv and result.json match the original documents (host check).");
  if (check.status === "citations_passed") return el("div", { class: "notice ok" }, `${check.count} quotes in result.json match the uploaded documents (host check).`);
  if (check.status === "failed") return el("div", { class: "notice err" }, `Host check failed: ${check.detail}`);
  return null;
}

async function renderFile(turn, file, body, download) {
  const url = `/api/threads/${encodeURIComponent(state.threadId)}/turns/${file.turn}/files/${file.path.split("/").map(encodeURIComponent).join("/")}`;
  const name = file.path.split("/").pop();
  const output = file.path.startsWith("output/");
  const unchecked = output && (turn.check?.unchecked || []).includes(name);
  const contract = ["comparison.md", "kpis.csv", "result.json"].includes(name) && output;
  const checkLabel = file.upload ? "uploaded · host copy"
    : contract && turn.check?.status === "passed" ? "checked"
    : output && name === "result.json" && turn.check?.status === "citations_passed" ? "citations checked"
    : unchecked ? "not host-checked" : "";
  $("artifact-title").textContent = name;
  $("artifact-sub").textContent = [file.path, file.too_large ? "over 1 MiB" : formatBytes(file.bytes),
    file.turn === turn.number ? `turn ${file.turn}` : `from turn ${file.turn}`, checkLabel].filter(Boolean).join(" · ");
  if (file.too_large) {
    body.replaceChildren(el("div", { class: "notice warn" }, "This file is larger than the 1 MiB transfer limit and was not copied."));
    return;
  }
  download.href = `${url}?download=1`;
  download.hidden = false;
  const viewKey = `${state.threadId}:${file.turn}:${file.path}`;
  body.dataset.view = viewKey;
  body.replaceChildren(el("div", { class: "empty" }, el("p", {}, "Loading…")));
  const response = await fetch(url);
  const text = response.ok ? await response.text() : "";
  if (body.dataset.view !== viewKey) return; // the user switched tabs meanwhile
  const nodes = [];
  if (unchecked && file.turn === turn.number) {
    const covered = state.thread?.sources === "uploads"
      ? "The host check covers the quotes in result.json."
      : "The host check covers comparison.md, kpis.csv and result.json.";
    nodes.push(el("div", { class: "notice warn" }, `Not checked on the host: ${name} is the agent's own work. ${covered}`));
  }
  if (!response.ok) nodes.push(el("div", { class: "notice err" }, "The file could not be loaded."));
  else if (name.endsWith(".md")) { body.className = "artifact-body document"; nodes.push(renderMarkdown(text)); }
  else if (name.endsWith(".csv")) nodes.push(el("div", { class: "padded" }, renderCsv(text)));
  else if (name.endsWith(".json")) nodes.push(renderCode(prettyJson(text)));
  else nodes.push(renderCode(text));
  body.replaceChildren(...nodes);
}

function formatBytes(bytes) {
  if (bytes === undefined) return "";
  return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB`;
}

function prettyJson(text) {
  try { return JSON.stringify(JSON.parse(text), null, 2); } catch { return text; }
}

function renderCode(text) {
  const lines = text.replace(/\n$/, "").split("\n");
  return el("div", { class: "code" }, lines.map((line, index) => el("div", { class: "line" }, el("span", {}, index + 1), el("span", {}, line))));
}

function inline(text) {
  // **bold** and `code`; everything else stays text.
  const parts = [];
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    const token = match[0];
    parts.push(token.startsWith("**") ? el("strong", {}, token.slice(2, -2)) : el("code", {}, token.slice(1, -1)));
    last = match.index + token.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

function splitRow(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}

function renderMarkdown(text) {
  const root = el("article", { class: "doc" });
  const lines = text.split("\n");
  let index = 0;
  let paragraph = [];
  const flush = () => {
    if (paragraph.length) root.append(el("p", {}, inline(paragraph.join(" "))));
    paragraph = [];
  };
  while (index < lines.length) {
    const line = lines[index];
    const heading = /^(#{1,3})\s+(.*)$/.exec(line);
    if (heading) {
      flush();
      root.append(el(`h${heading[1].length}`, {}, inline(heading[2])));
    } else if (/^\s*\|/.test(line) && /^\s*\|?\s*:?-{3,}/.test(lines[index + 1] || "")) {
      flush();
      const header = splitRow(line);
      const rows = [];
      index += 2;
      while (index < lines.length && /^\s*\|/.test(lines[index])) rows.push(splitRow(lines[index++]));
      index -= 1;
      root.append(table(header, rows, false));
    } else if (/^\s*[-*]\s+/.test(line)) {
      flush();
      const list = el("ul");
      while (index < lines.length && /^\s*[-*]\s+/.test(lines[index])) list.append(el("li", {}, inline(lines[index++].replace(/^\s*[-*]\s+/, ""))));
      index -= 1;
      root.append(list);
    } else if (!line.trim()) {
      flush();
    } else {
      paragraph.push(line.trim());
    }
    index += 1;
  }
  flush();
  return root;
}

function parseCsv(text) {
  const rows = [];
  let row = [], cell = "", quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (quoted) {
      if (char === '"' && text[i + 1] === '"') { cell += '"'; i += 1; }
      else if (char === '"') quoted = false;
      else cell += char;
    } else if (char === '"') quoted = true;
    else if (char === ",") { row.push(cell); cell = ""; }
    else if (char === "\n" || char === "\r") {
      if (char === "\r" && text[i + 1] === "\n") i += 1;
      row.push(cell); rows.push(row); row = []; cell = "";
    } else cell += char;
  }
  if (cell || row.length) { row.push(cell); rows.push(row); }
  return rows.filter((item) => item.some((value) => value !== ""));
}

function renderCsv(text) {
  const [header = [], ...rows] = parseCsv(text);
  return table(header, rows, true);
}

function table(header, rows, monospace) {
  const numeric = header.map((_, column) => rows.length > 0 && rows.every((row) => /^-?\d+(\.\d+)?%?$/.test(row[column] || "")));
  return el("div", { class: `table-box${monospace ? " mono-table" : ""}` }, el("table", {},
    el("thead", {}, el("tr", {}, header.map((cell, column) => el("th", { scope: "col", class: numeric[column] ? "num" : null }, cell)))),
    el("tbody", {}, rows.map((row) => el("tr", {}, header.map((_, column) => el("td", { class: numeric[column] ? "num" : null }, inline(row[column] || "")))))),
  ));
}

/* ---------- Trace ---------- */

async function renderTrace(turn, body) {
  $("artifact-title").textContent = `Turn ${turn.number} trace`;
  const shortId = `${turn.otel_trace_id.slice(0, 8)}…${turn.otel_trace_id.slice(-4)}`;
  $("artifact-sub").textContent = `trace ${shortId} · from Jaeger`;
  const viewKey = `${state.threadId}:${turn.number}:trace`;
  body.dataset.view = viewKey;
  if (!body.querySelector(".trace")) body.replaceChildren(el("div", { class: "empty" }, el("p", {}, "Loading the trace…")));
  const { status, body: data } = await getJSON(`/api/threads/${encodeURIComponent(state.threadId)}/turns/${turn.number}/trace`);
  if (body.dataset.view !== viewKey) return;
  const jaegerLink = state.config?.jaeger_url
    ? el("a", { href: `${state.config.jaeger_url}/trace/${turn.otel_trace_id}`, target: "_blank", rel: "noopener noreferrer", class: "mono" }, `${shortId} in Jaeger`)
    : null;
  if (status === 202) {
    body.replaceChildren(el("div", { class: "empty" }, el("p", {}, "Waiting for spans to reach Jaeger…")));
    setTimeout(() => { if (body.dataset.view === viewKey) renderTrace(turn, body); }, 2000);
    return;
  }
  if (status !== 200) {
    body.replaceChildren(el("div", { class: "notice warn" }, "Jaeger is not reachable from the UI server. Run local-kubernetes.sh ui, which port-forwards it.", " ", jaegerLink));
    return;
  }
  $("artifact-sub").textContent = `trace ${shortId} · ${data.span_count} spans · from Jaeger${turn.status === "running" ? " · live" : ""}`;
  const seconds = (ms) => (ms >= 1000 ? `${(ms / 1000).toFixed(ms >= 10000 ? 1 : 2)} s` : `${Math.round(ms)} ms`);
  const total = data.duration_ms || 1;
  const tile = (label, value, sub) => el("div", { class: "tile" }, el("span", { class: "label" }, label), el("span", { class: "value" }, value), el("span", { class: "sub" }, sub));
  const tokens = data.model.total_tokens;
  const tiles = el("div", { class: "tiles" },
    tile("Duration", seconds(total), turn.status === "running" ? "turn still running" : `Actor ${turn.lifecycle}, then suspended`),
    tile("Model", `${data.model.calls} call${data.model.calls === 1 ? "" : "s"}`, `${seconds(data.model.ms)} · ${turn.model}`),
    tile("Tokens", tokens.toLocaleString(), `${data.model.input_tokens.toLocaleString()} in · ${data.model.output_tokens.toLocaleString()} out`),
    tile("Sandbox", `${data.tools.calls} tool call${data.tools.calls === 1 ? "" : "s"}`, data.tools.failed ? `${data.tools.failed} failed` : "none failed"));

  const lifecycle = data.rows.filter((row) => row.kind === "life").reduce((sum, row) => sum + row.duration_ms, 0);
  const skill = data.rows.filter((row) => row.kind === "skill").reduce((sum, row) => sum + row.duration_ms, 0);
  const tools = data.tools.ms - skill;
  const parts = [
    ["Workspace", lifecycle, "k-life"], ["Skill load", skill, "k-skill"], ["Model", data.model.ms, "k-model"],
    ["Tool commands", tools, "k-tool"], ["Other", Math.max(total - lifecycle - skill - data.model.ms - tools, 0), "k-probe"],
  ].filter(([, ms]) => ms > 0);
  const split = el("div", { class: "split", role: "img", "aria-label": parts.map(([label, ms]) => `${label} ${seconds(ms)}`).join(", ") },
    parts.map(([, ms, kind]) => el("div", { class: kind, style: `flex: 0 0 ${(ms / total * 100).toFixed(2)}%` })));
  const legend = el("div", { class: "legend" }, parts.map(([label, ms, kind]) => el("span", {}, el("span", { class: `swatch ${kind}` }), `${label} ${seconds(ms)}`)));

  // About five ticks at 1, 2 or 5 × 10^n milliseconds, labelled in seconds.
  const rough = total / 5;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 5, 10].map((factor) => factor * magnitude).find((value) => value >= rough);
  const ticks = [];
  for (let at = 0; at < total * 0.95; at += step) ticks.push(at);
  const axis = el("div", { class: "wf-axis" }, ticks.map((at) => el("span", { style: `left: ${(at / total * 100).toFixed(2)}%` },
    `${Number((at / 1000).toPrecision(3))} s`)));
  const rows = data.rows.map((row) => el("div", { class: "wf-row" },
    el("span", { class: "wf-label" }, row.label, row.badge ? el("span", { class: `pill${row.error ? " err" : ""}` }, row.badge) : null),
    el("div", { class: "wf-track" }, el("div", {
      class: `wf-bar k-${row.kind}`,
      style: `left: ${(row.start_ms / total * 100).toFixed(2)}%; width: ${Math.max(row.duration_ms / total * 100, 0.4).toFixed(2)}%`,
    })),
    el("span", { class: "wf-dur" }, seconds(row.duration_ms))));
  const waterfall = el("div", { class: "waterfall" }, el("div", { class: "waterfall-inner" },
    el("div", { class: "wf-row wf-head" }, el("span", {}, "Span"), axis, el("span", { style: "text-align: right" }, "Duration")), rows));
  body.replaceChildren(el("div", { class: "trace" },
    tiles,
    el("div", {}, el("h3", { style: "margin: 0 0 10px; font-size: 14px" }, "Where the time went"), split, el("div", { style: "height: 10px" }), legend),
    waterfall,
    el("div", { class: "trace-foot" },
      el("span", {}, "Spans carry timing, tool names, exit codes and token counts only; no prompts, documents or commands leave the harness."),
      el("span", { style: "flex: 1 1 0" }), jaegerLink)));
  if (turn.status === "running") setTimeout(() => { if (body.dataset.view === viewKey) renderTrace(currentTurn() || turn, body); }, 3000);
}

/* ---------- Start ---------- */

async function main() {
  for (const button of document.querySelectorAll("[data-theme-choice]")) {
    button.addEventListener("click", () => {
      state.themeChoice = button.dataset.themeChoice;
      writeStorage("di-theme", state.themeChoice === "system" ? null : state.themeChoice);
      applyTheme();
    });
  }
  media.addEventListener("change", () => { if (state.themeChoice === "system") applyTheme(); });
  applyTheme();
  $("turn-select").addEventListener("change", (event) => openView(Number(event.target.value), defaultView(
    state.thread.turns.find((turn) => turn.number === Number(event.target.value)))));
  $("new-analysis").addEventListener("click", () => state.chatkit?.setThreadId(null));

  const { body: config } = await getJSON("/api/config");
  state.config = config;
  $("model-name").textContent = config.model;
  $("cluster-name").textContent = `${config.context} · ${config.atespace}`;
  if (config.jaeger_url) {
    $("jaeger-link").href = config.jaeger_url;
    $("jaeger-link").hidden = false;
  }
  state.threadId = readStorage("di-thread");
  render();
  if (state.threadId) refreshThread({ followLatest: true });
  await startChat();
}

main();
