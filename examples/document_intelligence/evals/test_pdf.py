# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

import json
import os
import shutil
from datetime import datetime

import pytest

pytest.importorskip("chatkit", reason="install the ui dependency group: uv sync --group ui")

from chatkit.types import ThreadMetadata  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from examples.document_intelligence.agent.lib import citations, pdf  # noqa: E402
from examples.document_intelligence.evals.test_ui import ShellSteps, events, message, post_chatkit, upload_chat  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0fake-jpeg"


def make_pdf(pages):
    """A minimal PDF with one Helvetica text block per page; an empty string gives a page without text."""
    def escape(line):
        return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        lines = " ".join(f"({escape(line)}) Tj T*" for line in text.split("\n")) if text else ""
        stream = f"BT /F1 12 Tf 72 720 Td 16 TL {lines} ET"
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        objects.append("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                       f"/Resources << /Font << /F1 3 0 R >> >> /Contents {len(objects)} 0 R >>")
        kids.append(len(objects))
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(f'{kid} 0 R' for kid in kids)}] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def summary(**overrides):
    value = {"pages": 2, "processed": 2, "texts": ["Revenue was USD 3.2 billion.", ""], "images": [len(JPEG), len(JPEG)]}
    return json.dumps({**value, **overrides}).encode()


def test_sandbox_output_is_validated_before_it_is_trusted():
    pages = pdf.parse(summary(), JPEG * 2)
    assert pages.processed == 2 and pages.text_pages == [1] and pages.images == [JPEG, JPEG]
    for bad, blob in [(summary(images=[len(JPEG), len(JPEG) + 1]), JPEG * 2),  # sizes disagree with the bytes
                      (summary(), b"<svg>" + JPEG[5:] + JPEG),                   # first page is not a JPEG
                      (summary(processed=3), JPEG * 2),                          # more pages than the PDF has
                      (summary(texts=["one"]), JPEG * 2),                        # one text for two pages
                      (b"not json", b"")]:
        with pytest.raises(pdf.PdfRejected):
            pdf.parse(bad, blob)


def test_quotes_match_across_ligatures_line_breaks_and_hyphenation():
    page = "The ﬁscal 2025 reve-\nnue was USD 3.2\n  billion."
    assert pdf.quote_on_page("fiscal 2025 revenue was USD 3.2 billion.", page)
    assert pdf.quote_on_page("reve- nue was", page)
    assert not pdf.quote_on_page("revenue was USD 4.2 billion", page)
    assert not pdf.quote_on_page("   ", page)


def pdf_thread(chat, pages):
    """A thread whose upload r.pdf was processed in turn 1, with these page texts on the host."""
    base = chat.output_dir / "thr_pdf" / "turn-1"
    for number, text in enumerate(pages, 1):
        target = base / pdf.page_file("r.pdf", number, "txt")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        (base / pdf.page_file("r.pdf", number, "jpg")).write_bytes(JPEG)
    (base / "inputs/uploads").mkdir(parents=True, exist_ok=True)
    (base / "inputs/uploads/r.pdf").write_bytes(b"%PDF-1.4\n")
    upload = {"name": "r.pdf", "path": "inputs/uploads/r.pdf", "bytes": 9, "sha256": "0" * 64, "turn": 1,
              "pdf": {"pages": len(pages), "processed": len(pages), "images": len(pages),
                      "text_pages": [number for number, text in enumerate(pages, 1) if text.strip()]}}
    files = [{"path": "inputs/uploads/r.pdf", "bytes": 9, "sha256": "0" * 64, "new": True, "changed": True,
              "upload": True, "pdf": upload["pdf"]}]
    return ThreadMetadata(id="thr_pdf", created_at=datetime.now(), metadata={
        "sources": "uploads", "uploads": [upload], "actor": "test-space/oas-x",
        "turns": [{"number": 1, "status": "completed", "files": files, "uploads": ["r.pdf"], "tools": [], "check": None}]})


def check(chat, thread, *cited):
    return citations.check_citations(chat.conversation(thread), {"result.json": json.dumps({"citations": list(cited)}).encode()})


async def test_pdf_citations_are_checked_against_the_cited_page(server, tmp_path):
    chat, _ = upload_chat(server, tmp_path, ShellSteps([]))
    thread = pdf_thread(chat, ["Orion revenue was USD 3.2 bil-\nlion in fiscal 2025.", "Headcount: 410", ""])
    passed = check(chat, thread, {"document_id": "r.pdf", "page": 1, "quote": "Orion revenue was USD 3.2 billion"},
                   {"document_id": "r.pdf", "page": 3, "quote": "Signed by the auditor"})
    assert passed["status"] == "citations_passed" and passed["count"] == 1 and passed["visual"] == 1
    assert [(item["page"], item["kind"]) for item in passed["citations"]] == [(1, "text"), (3, "visual")]
    image_only = check(chat, thread, {"document_id": "r.pdf", "page": 2, "quote": "Headcount 410", "source": "image"})
    assert image_only["status"] == "visual_only" and image_only["visual"] == 1
    wrong_page = check(chat, thread, {"document_id": "r.pdf", "page": 2, "quote": "Orion revenue was USD 3.2 billion"})
    assert wrong_page["status"] == "failed" and "page 2" in wrong_page["detail"]
    for citation in ({"document_id": "r.pdf", "quote": "Orion revenue"}, {"document_id": "r.pdf", "page": 9, "quote": "x"},
                     {"document_id": "r.pdf", "page": "1", "quote": "Orion revenue"}):
        assert check(chat, thread, citation)["status"] == "failed"


async def test_a_turn_that_leaves_result_json_alone_reports_no_new_citations(server, tmp_path):
    chat, _ = upload_chat(server, tmp_path, ShellSteps([]))
    thread = pdf_thread(chat, ["Orion revenue was USD 3.2 billion.", ""])
    earlier = check(chat, thread, {"document_id": "r.pdf", "page": 1, "quote": "Orion revenue was USD 3.2 billion"},
                    {"document_id": "r.pdf", "page": 2, "quote": "Signed", "source": "image"}) | {"scope": "citations"}

    def card(changed):
        turn = {"number": 2, "uploads": [], "tools": [], "duration_s": 1, "lifecycle": "resumed", "check": earlier,
                "files": [{"path": "output/result.json", "changed": changed}]}
        return json.dumps(chat._turn_card(chat.conversation(thread), {}, turn).item.widget.model_dump())

    assert "No new citations this turn" in card(False)
    assert "matches the source documents" not in card(False) and "to review" not in card(False)
    assert "1 quote matches the source documents" in card(True) and "1 quote from page images to review" in card(True)


async def test_pdf_pages_are_served_as_images_and_the_pdf_only_as_a_download(server, tmp_path):
    chat, app = upload_chat(server, tmp_path, ShellSteps([]))
    thread = pdf_thread(chat, ["page one text", ""])
    await chat.store.save_thread(thread, {})
    with TestClient(app, base_url="http://localhost:8000") as http:
        image = http.get("/api/threads/thr_pdf/turns/1/files/inputs/derived/r.pdf/page-001.jpg")
        assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg" and image.content == JPEG
        assert image.headers["content-security-policy"] == "sandbox"
        text = http.get("/api/threads/thr_pdf/turns/1/files/inputs/derived/r.pdf/page-001.txt")
        assert text.headers["content-type"].startswith("text/plain") and text.text == "page one text"
        assert http.get("/api/threads/thr_pdf/turns/1/files/inputs/derived/r.pdf/page-003.jpg").status_code == 404
        assert http.get("/api/threads/thr_pdf/turns/1/files/inputs/derived/other.pdf/page-001.jpg").status_code == 404
        original = http.get("/api/threads/thr_pdf/turns/1/files/inputs/uploads/r.pdf")
        assert original.headers["content-type"] == "application/octet-stream"
        assert original.headers["content-disposition"].startswith("attachment")


@pytest.mark.skipif(not shutil.which("pdftoppm", path=os.defpath), reason="poppler-utils is not installed on this host")
async def test_an_uploaded_pdf_is_extracted_rendered_and_cited_by_page(server, tmp_path):
    report = make_pdf(["Orion Labs fiscal 2025 summary", "Orion Labs revenue was USD 3.2 billion in fiscal 2025.", ""])
    citations = json.dumps({"citations": [{"document_id": "orion.pdf", "page": 2,
                                           "quote": "Orion Labs revenue was USD 3.2 billion in fiscal 2025."}]})
    model = ShellSteps(["cat inputs/uploads/MANIFEST.json inputs/derived/orion.pdf/page-002.txt",
                        f"printf '%s' '{citations}' > output/result.json"], answer="Revenue: USD 3.2 billion (page 2).")
    chat, app = upload_chat(server, tmp_path, model)
    with TestClient(app, base_url="http://localhost:8000") as http:
        attachment = post_chatkit(http, {"type": "attachments.create", "params": {
            "name": "orion.pdf", "size": len(report), "mime_type": "application/pdf"}})
        assert http.put("/uploads/" + attachment["id"], content=report).status_code == 200
        stream = await events(chat, {"type": "threads.create", "params": {"input": message("Orion revenue?", [attachment["id"]])}})
        assert not [event for event in stream if event["type"] == "error"], stream
        thread_id = next(event["thread"]["id"] for event in stream if event["type"] == "thread.created")
        assert '"processed": 3' in model.outputs[0] and "Orion Labs revenue was USD 3.2 billion" in model.outputs[0]
        state = http.get(f"/api/threads/{thread_id}").json()
        info = state["uploads"][0]["pdf"]
        assert info == {"pages": 3, "processed": 3, "text_pages": [1, 2], "images": 3}
        assert state["turns"][0]["check"]["status"] == "citations_passed"
        page = http.get(f"/api/threads/{thread_id}/turns/1/files/inputs/derived/orion.pdf/page-002.jpg")
        assert page.status_code == 200 and page.content.startswith(b"\xff\xd8\xff")
        assert "Copied 1 upload" in json.dumps(stream) and "3 pages, 2 with text" in json.dumps(stream)
        assert not list(server.root.rglob(".pdf-*"))
