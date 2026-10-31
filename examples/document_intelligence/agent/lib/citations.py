# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Host checks of a conversation turn: the sample-report contract and quotes from the source documents.

Quotes are checked against the host's trusted copies of each source (sample
inputs, uploads and PDF page text), never against files the sandbox could edit.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import pdf
from .verify import OUTPUTS, verify_artifacts

TEXT_TYPES = {".md", ".txt", ".csv"}
# Checked quotes listed for the UI, which shows each one in the host's copy of its source.
MAX_LISTED = 80


def clipped(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n… {len(text) - limit} more characters"


def check_turn(conversation, files: dict[str, bytes], inputs: dict[str, bytes]) -> dict:
    outputs = {name.removeprefix("output/"): data for name, data in files.items() if name.startswith("output/")}
    samples = conversation.metadata["sources"] == "samples"
    sample_check = check_samples(outputs, inputs, conversation.metadata["analysis_id"]) if samples else None
    if sample_check and sample_check["status"] == "passed":
        # The contract check compared result.json's citations with the reports; list them for the UI.
        sample_check.update(quotes_from="output/result.json",
                            checked_quotes=listed_quotes(outputs["result.json"], citation_sources(conversation, inputs)))
    if sample_check and (sample_check["status"] == "passed" or not conversation.uploads):
        return sample_check
    sources = citation_sources(conversation, inputs if samples else None)
    checked = check_citations(conversation, outputs, sources=sources)
    checked["scope"] = "citations"
    if checked["status"] == "citations_passed" and sample_check:
        uses_uploads = any(sources[resolve_source(document, sources)]["uploaded"] for document in checked["documents"])
        if not uses_uploads:
            # Matching sample quotes cannot hide a broken sample calculation or CSV.
            return sample_check
    return checked


def check_samples(outputs: dict[str, bytes], inputs: dict[str, bytes], analysis_id: str) -> dict:
    extra = sorted(name for name in outputs if name not in OUTPUTS)
    if not set(OUTPUTS) <= outputs.keys():
        return {"status": "not_written", "unchecked": extra}
    try:
        verify_artifacts({name: outputs[name] for name in OUTPUTS}, inputs, analysis_id)
    except ValueError as error:
        return {"status": "failed", "detail": str(error), "unchecked": extra}
    return {"status": "passed", "unchecked": extra}


def citation_sources(conversation, inputs: dict[str, bytes] | None = None) -> dict:
    sources = {}
    for name, data in (inputs or {}).items():
        if Path(name).suffix in TEXT_TYPES:
            sources[f"inputs/{name}"] = {"name": name, "text": " ".join(data.decode("utf-8").split()), "uploaded": False}
    for upload in conversation.uploads:
        if upload.get("pdf"):
            # The host's copy of each page's text, taken before the model ran.
            base = conversation.turn_dir(upload["turn"])
            pages = [(base / pdf.page_file(upload["name"], number, "txt")).read_text(encoding="utf-8")
                     for number in range(1, upload["pdf"]["processed"] + 1)]
            sources[upload["path"]] = {"name": upload["name"], "text": "", "pages": pages, "uploaded": True}
            continue
        text = conversation.host_copy(upload).read_text(encoding="utf-8")
        sources[upload["path"]] = {"name": upload["name"], "text": " ".join(text.split()), "uploaded": True}
    return sources


def listed_quotes(result: bytes, sources: dict) -> list[dict]:
    """result.json's citations as source path and quote, for a contract that already passed."""
    try:
        citations = json.loads(result)["citations"]
    except (ValueError, KeyError, TypeError):
        return []
    listed = []
    for citation in citations if isinstance(citations, list) else []:
        if not isinstance(citation, dict) or not isinstance(citation.get("quote"), str):
            continue
        try:
            source = resolve_source(citation.get("document_id"), sources)
        except ValueError:
            continue
        if source is not None:
            listed.append({"source": source, "quote": clipped(citation["quote"], 240),
                           "context": citation.get("section") if isinstance(citation.get("section"), str) else None})
    return listed[:MAX_LISTED]


def resolve_source(document, sources: dict) -> str | None:
    """Paths and exact filenames take priority; stems must resolve uniquely."""
    if not isinstance(document, str):
        return None
    if document in sources:
        return document
    matches = [key for key, source in sources.items() if source["name"] == document]
    if not matches:
        matches = [key for key, source in sources.items() if Path(source["name"]).stem == document]
    if len(matches) > 1:
        raise ValueError(f"Document ID {document!r} is ambiguous; cite the full inputs/ path.")
    return matches[0] if matches else None


def check_citations(conversation, outputs: dict[str, bytes], *, sources=None) -> dict:
    """Check quotes against trusted host sources, never against sandbox-edited inputs."""
    unchecked = sorted(name for name in outputs if name != "result.json")
    if "result.json" not in outputs:
        return {"status": "no_citations", "unchecked": unchecked}
    if sources is None:
        sources = citation_sources(conversation)
    try:
        citations = json.loads(outputs["result.json"])["citations"]
        if not isinstance(citations, list):
            raise TypeError
    except (ValueError, KeyError, TypeError):
        return {"status": "failed", "detail": "output/result.json has no citations list.", "unchecked": unchecked}
    documents = set()
    checked = visual = 0
    pages = []  # page-level PDF citations, for the panel
    listed = []  # every checked quote and its source, for the panel
    for citation in citations:
        document = citation.get("document_id") if isinstance(citation, dict) else None
        quote = citation.get("quote") if isinstance(citation, dict) else None
        try:
            source_id = resolve_source(document, sources)
        except ValueError as error:
            return {"status": "failed", "detail": str(error), "unchecked": unchecked}
        if source_id is None:
            return {"status": "failed", "detail": f"A citation names {document!r}, which is not a source file.",
                    "unchecked": unchecked}
        source = sources[source_id]
        shown = clipped(str(quote), 120)
        if "pages" in source:
            page = citation.get("page")
            if type(page) is not int or not 1 <= page <= len(source["pages"]):
                return {"status": "failed", "unchecked": unchecked, "detail":
                        f"A citation of {document} needs a page number from 1 to {len(source['pages'])}."}
            if not isinstance(quote, str) or not quote.strip():
                return {"status": "failed", "detail": f"A citation of page {page} of {document} has no quote.",
                        "unchecked": unchecked}
            # A page without a text layer can only be read from its image: a person reviews those.
            is_visual = citation.get("source") == "image" or not source["pages"][page - 1].strip()
            if not is_visual and not pdf.quote_on_page(quote, source["pages"][page - 1]):
                return {"status": "failed", "detail": f"Not found on page {page} of {document}: {shown}",
                        "unchecked": unchecked}
            visual += is_visual
            checked += not is_visual
            pages.append({"document": source["name"], "page": page, "kind": "visual" if is_visual else "text",
                          "quote": clipped(quote, 240)})
            listed.append({"source": source_id, "page": page, "kind": "visual" if is_visual else "text",
                           "quote": clipped(quote, 240)})
        elif not isinstance(quote, str) or not quote.strip() or " ".join(quote.split()) not in source["text"]:
            return {"status": "failed", "detail": f"Not found in {document}: {shown}", "unchecked": unchecked}
        else:
            checked += 1
            listed.append({"source": source_id, "quote": clipped(quote, 240)})
        documents.add(document)
    if not documents:
        return {"status": "no_citations", "unchecked": unchecked}
    return {"status": "citations_passed" if checked else "visual_only", "count": checked, "visual": visual,
            "documents": sorted(documents), "citations": pages, "unchecked": unchecked,
            "checked_quotes": listed[:MAX_LISTED], "quotes_from": "output/result.json"}
