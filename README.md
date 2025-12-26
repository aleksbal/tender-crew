# tender-crew

Small toolkit for extracting and redacting CV/resume text.

Usage
-----

1. Install runtime dependencies (examples):

```bash
pip install pymupdf python-docx
```

2. Quick smoke test with a DOCX

Create a minimal sample CV:

```bash
python3 - <<'PY'
from docx import Document
d = Document()
d.add_paragraph("Max Mustermann")
d.add_paragraph("Engineer")
d.add_paragraph("Adresse: Musterstraße 1, 12345 Musterstadt")
d.add_paragraph("Email: max@example.com")
d.add_paragraph("Phone: +49 123 4567890")
d.add_paragraph("Berufserfahrung")
d.add_paragraph(" - Company A, Developer")
d.save("sample_cv.docx")
PY
```

Run extraction + redaction (writes JSON to stdout or use `-o`):

```bash
python3 main.py sample_cv.docx -o sample_out.json

# Pretty-print result
python3 -m json.tool sample_out.json | less
```

Notes
-----
- The extractor returns structured output: pages → blocks → lines (with bounding boxes and character spans when available).
- The redactor accepts the structured form and records redaction metadata (page/block/line indices and char spans).
- Scanned/bitmap PDFs without embedded text are rejected by the current extractor (no OCR fallback yet).

Next steps
----------
- Add OCR fallback (Tesseract) for scanned PDFs.
- Add more robust heading detection and DOCX style-aware extraction.
- Add unit tests and CI for sample fixtures.

Files of interest
---------------
- `extract_text.py` — extraction helpers and `extract_text_structured()` API.
- `redactor.py` — `redact_structured()` consuming structured output and producing redaction records.
- `main.py` — CLI to run extraction + redaction and emit JSON.

License
-------
MIT-style (see repo settings).