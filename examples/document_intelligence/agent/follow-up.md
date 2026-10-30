This is a multi-turn conversation in one persistent workspace. Files from earlier
turns stay in inputs/, work/ and output/. For a follow-up question, start from the
existing files and the cited sources, write new code only when the question needs
it, and save any new result in output/ under a descriptive name. Do not rewrite
comparison.md, kpis.csv or result.json unless the user asks for it, except to add
citations for uploaded documents as described below.

The user may upload documents. They arrive in inputs/uploads/, listed with their
SHA-256 in inputs/uploads/MANIFEST.json. The document-analysis skill's extractor
supports only the bundled sample reports: analyze uploaded documents with the shell
and Python instead. When you report facts from uploaded documents, also write
output/result.json with a "citations" list of {"document_id": "<file name>",
"quote": "<text copied exactly from that file>"}; the host checks every quote
against its retained source bytes. In a follow-up turn, add the new answer's
citations to that list and keep the earlier ones. Use a full inputs/ path when file names or
short document IDs are ambiguous. Mixed reports may cite both the sample reports
and uploads; their calculated figures are not covered by the citation check.

Uploaded PDFs are already processed: inputs/derived/<file name>/page-NNN.txt holds
each page's extracted text and page-NNN.jpg its image (MANIFEST.json lists the
pages with a text layer). Read the text first. Use the view_image tool on a page
image for tables, charts, or pages with little or no text. Cite a PDF with
{"document_id": "<file name>", "page": <page number>, "quote": "<exact text>"};
the host checks the quote against that page's extracted text. For text you could
only read from a page image, add "source": "image"; those citations are shown
for a person to review instead of being checked.
