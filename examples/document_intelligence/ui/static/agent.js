// Copyright 2026 openai-agent-substrate contributors
// SPDX-License-Identifier: Apache-2.0
//
// Document Intelligence's own file view for the shared UI: an uploaded PDF as the
// page images and text the sandbox extracted, with the quotes cited from each page.
// The browser never parses the PDF. Everything here is inserted as text.

export async function renderFile({ file, turn, threadId, body, el, page }) {
  if (!file.pdf) return false;
  const name = file.path.split("/").pop();
  const info = file.pdf;
  const base = `/api/threads/${encodeURIComponent(threadId)}/turns/${file.turn}/files/inputs/derived/${encodeURIComponent(name)}`;
  const textPages = new Set(info.text_pages);
  const cited = (turn?.check?.citations || []).filter((citation) => citation.document === name);
  const summary = `${info.pages} page${info.pages === 1 ? "" : "s"}, ${info.text_pages.length} with a text layer` +
    (info.processed < info.pages ? `; the first ${info.processed} were processed` : "") +
    ". The sandbox extracted and rendered these pages before the model read them; quotes from pages without text are marked for review.";
  const grid = el("div", { class: "pages" });
  for (let number = 1; number <= info.processed; number += 1) {
    const padded = String(number).padStart(3, "0");
    const quotes = cited.filter((citation) => citation.page === number);
    const textBox = el("pre", { class: "page-text", hidden: true });
    const toggle = el("button", { type: "button", class: "text-toggle", "aria-expanded": "false" }, "Show text");
    toggle.addEventListener("click", async () => {
      const open = textBox.hidden;
      if (open && !textBox.dataset.loaded) {
        const response = await fetch(`${base}/page-${padded}.txt`);
        textBox.textContent = response.ok ? (await response.text()).trim() || "(no text on this page)" : "The text could not be loaded.";
        textBox.dataset.loaded = "1";
      }
      textBox.hidden = !open;
      toggle.setAttribute("aria-expanded", String(open));
      toggle.textContent = open ? "Hide text" : "Show text";
    });
    grid.append(el("figure", { class: `page-card${quotes.length ? " cited" : ""}`, "data-page": number },
      el("img", { src: `${base}/page-${padded}.jpg`, alt: `Page ${number} of ${name}`, loading: "lazy" }),
      el("figcaption", {},
        el("span", { class: "page-number" }, `Page ${number}`),
        textPages.has(number) ? null : el("span", { class: "pill" }, "no text layer"),
        quotes.length ? el("span", { class: "badge" }, `cited ×${quotes.length}`) : null),
      quotes.map((citation) => el("blockquote", { class: `page-quote ${citation.kind}` },
        el("span", { class: "quote-kind" }, citation.kind === "text" ? "Checked against the page text" : "Read from the image · review"),
        `“${citation.quote}”`)),
      textPages.has(number) ? toggle : null,
      textBox));
  }
  body.replaceChildren(el("div", { class: "pdf-view" }, el("p", { class: "pdf-summary" }, summary), grid));
  // A quote's "Show in source" names its page: scroll there once the pages above it have their height.
  const target = page && grid.querySelector(`[data-page="${Number(page)}"]`);
  if (target) {
    const above = [...grid.querySelectorAll("img")].slice(0, Number(page));
    for (const image of above) image.loading = "eager";
    Promise.all(above.map((image) => image.complete ? null
      : new Promise((resolve) => { image.addEventListener("load", resolve, { once: true }); image.addEventListener("error", resolve, { once: true }); })))
      .then(() => target.scrollIntoView({ block: "start" }));
  }
  return true;
}
