// Copyright 2026 openai-agent-substrate contributors
// SPDX-License-Identifier: Apache-2.0
//
// A turn's trace as the harness/sandbox split: one row for the workspace's lifecycle,
// one for what ran in the harness (model calls, harness tools, the host check) and
// one for what ran in the sandbox. Jaeger has the span-level detail.

import { el, getJSON, plural, seconds } from "./dom.js";

const LANES = [
  { id: "workspace", label: "Workspace", kinds: ["life"] },
  { id: "harness", label: "Harness", kinds: ["model", "harness", "check"] },
  { id: "sandbox", label: "Sandbox", kinds: ["tool", "skill", "error"] },
];
const KIND_LABELS = {
  life: "workspace lifecycle", model: "model call", harness: "harness tool", check: "host check",
  tool: "sandbox command", skill: "skill load", error: "failed command",
};

function checkSummary(check) {
  const status = check?.status || "";
  if (status.includes("failed")) return { value: "failed", tone: "err" };
  if (status.includes("passed")) return { value: "passed", tone: "ok" };
  return { value: "not checked", tone: "" };
}

function tile(label, value, sub, tone = "") {
  return el("div", { class: `tile ${tone}` }, el("span", { class: "label" }, label), el("span", { class: "value" }, value),
    sub ? el("span", { class: "sub" }, sub) : null);
}

function axis(total) {
  // About five ticks at 1, 2 or 5 × 10^n milliseconds, labelled in seconds.
  const rough = total / 5;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 5, 10].map((factor) => factor * magnitude).find((value) => value >= rough);
  const ticks = [];
  for (let at = 0; at < total * 0.95; at += step) ticks.push(at);
  return el("div", { class: "lane-row" }, el("span"), el("div", { class: "tl-axis" },
    ticks.map((at) => el("span", { style: `left: ${(at / total * 100).toFixed(2)}%` }, `${Number((at / 1000).toPrecision(3))} s`))));
}

export async function renderTrace({ threadId, turn, body, config, setHead, isCurrent, onLive }) {
  if (!turn.otel_trace_id) {
    setHead(`Turn ${turn.number} trace`, "tracing is off");
    body.replaceChildren(el("div", { class: "empty" },
      el("h3", {}, "Tracing is off"),
      el("p", {}, "Run ./scripts/local-kubernetes.sh tracing-setup, then restart this UI. Each later turn then has a trace: " +
        "what ran in the harness, what ran in the sandbox, and how long the workspace took to start.")));
    return;
  }
  const shortId = `${turn.otel_trace_id.slice(0, 8)}…${turn.otel_trace_id.slice(-4)}`;
  setHead(`Turn ${turn.number} trace`, `trace ${shortId} · from Jaeger`);
  if (!body.querySelector(".trace")) body.replaceChildren(el("div", { class: "empty" }, el("p", {}, "Loading the trace…")));
  const { status, body: data } = await getJSON(`/api/threads/${encodeURIComponent(threadId)}/turns/${turn.number}/trace`);
  if (!isCurrent()) return;
  const jaeger = config.jaeger_url
    ? el("a", { href: `${config.jaeger_url}/trace/${turn.otel_trace_id}`, target: "_blank", rel: "noopener noreferrer" }, "Open in Jaeger ↗")
    : null;
  if (status === 202) {
    body.replaceChildren(el("div", { class: "empty" }, el("p", {}, "Waiting for spans to reach Jaeger…")));
    setTimeout(() => { if (isCurrent()) onLive(); }, 2000);
    return;
  }
  if (status !== 200) {
    body.replaceChildren(el("div", { class: "notice warn" },
      "Jaeger is not reachable from the UI server. The local-kubernetes.sh UI commands port-forward it.", " ", jaeger));
    return;
  }
  const running = turn.status === "running";
  setHead(`Turn ${turn.number} trace`, `trace ${shortId} · ${data.span_count} spans${running ? " · live" : ""}`);
  const total = data.duration_ms || 1;
  const check = checkSummary(turn.check);
  const start = turn.start_s !== undefined && turn.start_s !== null ? `${turn.lifecycle} in ${turn.start_s} s` : turn.lifecycle;
  const tiles = el("div", { class: "tiles" },
    tile("Turn", seconds(total), running ? "still running" : `${plural(data.tools.calls + (data.harness_tools?.calls || 0), "tool call")}`),
    tile("Workspace", start || "—", turn.waited_s ? `waited ${Math.round(turn.waited_s)} s for a worker` : "then suspended"),
    tile("Model", plural(data.model.calls, "call"), `${data.model.total_tokens.toLocaleString()} tokens · ${seconds(data.model.ms)}`),
    tile("Host check", running ? "after the turn" : check.value, null, running ? "" : check.tone));

  const detail = el("div", { class: "tl-detail", "aria-live": "polite" },
    el("span", { class: "muted" }, "Select a step to see where it ran and how long it took."));
  const show = (row, lane) => detail.replaceChildren(
    el("div", { class: "tl-detail-head" }, el("strong", {}, row.label), el("span", { class: `tag tag-${lane.id}` }, lane.label.toLowerCase()),
      row.badge ? el("span", { class: `pill${row.error ? " err" : ""}` }, row.badge) : null,
      el("span", { class: "muted tl-detail-time" }, `at ${seconds(row.start_ms)} · ${seconds(row.duration_ms)}`)),
    el("span", { class: "muted" }, lane.id === "harness"
      ? (row.kind === "model" ? "A model call from the harness, which holds the API key." : row.kind === "check"
        ? "The host check, on the host's copies of the files, outside the sandbox." : "A harness tool: it runs outside the sandbox with the harness's credentials and saves its output as evidence.")
      : lane.id === "sandbox" ? "Ran inside the conversation's Actor, which has no credentials."
      : "Creating, resuming, connecting to or suspending the conversation's Actor."));

  const lanes = LANES.map((lane) => {
    const rows = data.rows.filter((row) => lane.kinds.includes(row.kind));
    return el("div", { class: "lane-row" },
      el("span", { class: "lane-label" }, lane.label, el("span", { class: "muted" }, ` ${rows.length}`)),
      el("div", { class: "lane-track" }, rows.map((row) => el("button", {
        type: "button", class: `tl-bar k-${row.kind}`, title: `${row.label} · ${seconds(row.duration_ms)}`,
        "aria-label": `${row.label}, ${KIND_LABELS[row.kind] || row.kind}, ${seconds(row.duration_ms)}`,
        style: `left: ${(row.start_ms / total * 100).toFixed(2)}%; width: ${Math.max(row.duration_ms / total * 100, 0.6).toFixed(2)}%`,
        onclick: () => show(row, lane),
      }))));
  });
  const legend = el("div", { class: "legend" }, ["model", "harness", "check", "tool", "skill", "life"].map((kind) =>
    el("span", {}, el("span", { class: `swatch k-${kind}` }), KIND_LABELS[kind])));
  const steps = el("details", { class: "tl-steps" }, el("summary", {}, `Every step (${data.rows.length})`),
    el("div", {}, data.rows.map((row) => {
      const lane = LANES.find((item) => item.kinds.includes(row.kind)) || LANES[0];
      return el("button", { type: "button", class: "tl-step", onclick: () => show(row, lane) },
        el("span", { class: `swatch k-${row.kind}` }), el("span", { class: "tl-step-label" }, row.label),
        row.badge ? el("span", { class: `pill${row.error ? " err" : ""}` }, row.badge) : null,
        el("span", { class: "muted" }, seconds(row.duration_ms)));
    })));
  body.replaceChildren(el("div", { class: "trace" },
    tiles,
    el("div", { class: "timeline" }, axis(total), lanes),
    detail, legend, steps,
    el("div", { class: "trace-foot" },
      el("span", {}, "Spans carry timing, tool names, exit codes and token counts only; prompts, files and commands stay in the harness."),
      el("span", { style: "flex: 1 1 0" }), jaeger)));
  if (running) setTimeout(() => { if (isCurrent()) onLive(); }, 3000);
}
