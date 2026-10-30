# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""PDF uploads: text and page images are produced inside the Actor, then checked copies kept on the host.

The PDF is untrusted, so poppler parses it in the sandbox, never in this harness.
The server runs the extraction itself, before the model sees the workspace, and
copies the results out; citation checks compare against those host copies.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

MAX_PDF_PAGES = 30
RENDER_DPI = 100
MAX_PAGE_IMAGE_BYTES = 4 * 1024 * 1024
PROCESS_TIMEOUT_S = 300
_JPEG = b"\xff\xd8\xff"

# Runs in the Actor with the sandbox image's poppler. Arguments: pdf, derived dir,
# scratch dir, page cap, DPI. Writes page-NNN.txt and page-NNN.jpg for the agent,
# and pages.json plus pages.bin (the images, concatenated) for the server to copy out.
PROCESS_SCRIPT = r"""
import json, os, subprocess, sys
pdf, derived, scratch, max_pages, dpi = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5]
os.makedirs(derived, exist_ok=True)
os.makedirs(scratch, exist_ok=True)
info = subprocess.run(["pdfinfo", pdf], capture_output=True, text=True, timeout=60)
if info.returncode != 0:
    sys.exit(3)
fields = dict(line.split(":", 1) for line in info.stdout.splitlines() if ":" in line)
pages = int(fields.get("Pages", "0").strip() or 0)
processed = min(pages, max_pages)
texts, sizes = [], []
open(os.path.join(scratch, "pages.bin"), "wb").close()
if processed:
    raw = os.path.join(scratch, "text.txt")
    subprocess.run(["pdftotext", "-enc", "UTF-8", "-f", "1", "-l", str(processed), pdf, raw], check=True, timeout=120)
    with open(raw, encoding="utf-8", errors="replace") as handle:
        split = handle.read().split("\f")
    subprocess.run(["pdftoppm", "-f", "1", "-l", str(processed), "-r", dpi, "-jpeg", "-jpegopt", "quality=80",
                    pdf, os.path.join(scratch, "render")], check=True, timeout=300)
    rendered = {int(name[7:-4]): os.path.join(scratch, name) for name in os.listdir(scratch)
                if name.startswith("render-") and name.endswith(".jpg")}
    with open(os.path.join(scratch, "pages.bin"), "wb") as blob:
        for number in range(1, processed + 1):
            text = split[number - 1] if number - 1 < len(split) else ""
            with open(os.path.join(derived, "page-%03d.txt" % number), "w", encoding="utf-8") as handle:
                handle.write(text)
            texts.append(text)
            data = b""
            if number in rendered:
                with open(rendered[number], "rb") as handle:
                    data = handle.read()
                os.replace(rendered[number], os.path.join(derived, "page-%03d.jpg" % number))
            blob.write(data)
            sizes.append(len(data))
with open(os.path.join(scratch, "pages.json"), "w", encoding="utf-8") as handle:
    json.dump({"pages": pages, "processed": processed, "texts": texts, "images": sizes}, handle)
"""


class PdfRejected(ValueError):
    """The PDF could not be processed; the message is safe to show."""


@dataclass
class PdfPages:
    pages: int
    texts: list[str]
    images: list[bytes]

    @property
    def processed(self) -> int:
        return len(self.texts)

    @property
    def text_pages(self) -> list[int]:
        return [number for number, text in enumerate(self.texts, 1) if text.strip()]


def derived_dir(name: str) -> str:
    return f"inputs/derived/{name}"


def page_file(name: str, page: int, extension: str) -> str:
    return f"{derived_dir(name)}/page-{page:03d}.{extension}"


def parse(summary_bytes: bytes, blob: bytes) -> PdfPages:
    """Validate what the sandbox reported; nothing in it is trusted."""
    try:
        summary = json.loads(summary_bytes)
        pages, processed = summary["pages"], summary["processed"]
        texts, sizes = summary["texts"], summary["images"]
    except (ValueError, KeyError, TypeError):
        raise PdfRejected("The PDF summary from the sandbox is malformed.") from None
    if (type(pages) is not int or type(processed) is not int or not 0 <= processed <= min(pages, MAX_PDF_PAGES)
            or not isinstance(texts, list) or not isinstance(sizes, list)
            or len(texts) != processed or len(sizes) != processed
            or not all(isinstance(text, str) for text in texts)
            or not all(type(size) is int and 0 <= size <= MAX_PAGE_IMAGE_BYTES for size in sizes)
            or sum(sizes) != len(blob)):
        raise PdfRejected("The PDF summary from the sandbox does not match its pages.")
    images, offset = [], 0
    for size in sizes:
        image = blob[offset:offset + size]
        if image and not image.startswith(_JPEG):
            raise PdfRejected("A rendered PDF page is not a JPEG image.")
        images.append(image)
        offset += size
    return PdfPages(pages=pages, texts=texts, images=images)


async def process(session, pdf_path: str, name: str, scratch: str) -> PdfPages:
    """Extract text and render pages inside the Actor, then copy the results out."""
    result = await session.exec("python3", "-c", PROCESS_SCRIPT, pdf_path, derived_dir(name), scratch,
                                str(MAX_PDF_PAGES), str(RENDER_DPI), shell=False, timeout=PROCESS_TIMEOUT_S)
    try:
        if not result.ok():
            raise PdfRejected(f"{name} could not be read as a PDF (it may be damaged or password-protected).")
        with await session.read(Path(scratch) / "pages.json") as handle:
            summary = handle.read()
        with await session.read(Path(scratch) / "pages.bin") as handle:
            blob = handle.read()
        return parse(summary, blob)
    finally:
        await session.exec("rm", "-rf", scratch, shell=False)


def normalize(text: str) -> str:
    """Compatibility forms (ligatures, full-width) and whitespace do not count as differences."""
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _dehyphenate(text: str) -> str:
    # Rejoins words hyphenated across a line break, which extraction leaves as "reve- nue".
    return re.sub(r"(\w)- (\w)", r"\1\2", text)


def quote_on_page(quote: str, page_text: str) -> bool:
    wanted, page = normalize(quote), normalize(page_text)
    return bool(wanted) and (wanted in page or _dehyphenate(wanted) in _dehyphenate(page))
