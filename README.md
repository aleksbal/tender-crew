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
- Scanned/bitmap PDFs without embedded text can be processed using the `--ocr` flag, which runs `ocrmypdf` (requires the system packages listed below).
- Conversion and preprocessing are delegated to a reusable `converter.py` component so the CLI remains lightweight and the conversion logic can be reused in larger pipelines.

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

Preprocessing tools
-------------------
Some preprocessing features require system binaries. Below are the recommended system packages and how they integrate:

Required system packages for OCR (`--ocr` / `ocrmypdf`):

```bash
sudo apt update
# OCR engine
sudo apt install -y tesseract-ocr
# PDF toolkit used by ocrmypdf
sudo apt install -y qpdf
# Ghostscript / poppler utilities used for PDF handling
sudo apt install -y ghostscript poppler-utils
```

Optional language packs for Tesseract (example German):

```bash
sudo apt install -y tesseract-ocr-deu
```

Python packages that enable OCR and preprocessing are listed in `requirements.txt` (e.g. `ocrmypdf`, `pytesseract`, `Pillow`). Note: the Python `ocrmypdf` package still requires the system binaries above.

Optional system tools (not required by default):

```bash
# Pandoc (DOCX -> structured JSON AST)
sudo apt install -y pandoc

# LibreOffice (soffice) can render DOCX -> PDF if you explicitly want layout bboxes,
# but it is heavy and not required by default. Avoid unless you need exact visual rendering.
sudo apt install -y libreoffice  # optional
```

CLI flags recap:

- `--ocr` (PDF only): run `ocrmypdf` before extraction to make scanned PDFs searchable. Requires the system packages above.
- `--pandoc-ast` (DOCX only): produce a `pandoc` JSON AST and include it in the output JSON (requires `pandoc`).

Using OpenAI via `--llm-kind`
-----------------------------
You can use OpenAI as the LLM provider by selecting `--llm-kind openai`. The CLI will call the LLM client which in turn uses the `openai` Python package. Set your API key in the environment first:

```bash
export OPENAI_API_KEY="sk_your_key_here"
python3 main.py resume.docx --llm --llm-kind openai --llm-model gpt-4o-mini -o out.json
```

If `OPENAI_API_KEY` is not set the OpenAI client will raise an error. The default LLM provider remains `ollama`.

If system tools are not available the CLI prints a warning and falls back to the default extraction path.