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
d.add_paragraph("01/2022 02/2023")
d.add_paragraph(" - Company A, Developer")
d.add_paragraph(" - Working on a project with Java, MySQL and Gradle")
d.add_paragraph("03/2023 12/2025")
d.add_paragraph(" - Company B, Developer")
d.add_paragraph(" - Working on a project with Python and PostgreSQL")
d.save("sample_cv.docx")
PY
```

Run extraction + redaction (writes JSON to stdout or use `-o`):

```bash
python3 cv_llm_converter.py sample_cv.docx --llm --llm-kind ollama --llm-model gpt-oss:120b-cloud -o sample_out.json
# Pretty-print result
python3 -m json.tool sample_out.json | less
```

Notes
-----
- The text extractor returns flattened text output, it is in `cv_text_extractor.py` component.
- CLI (`cv_llm_converter.py`) remains lightweight and the conversion logic can be reused in larger pipelines.

Next steps
----------
- Add more robust heading detection and DOCX style-aware extraction.
- PII !!!
- Add unit tests and CI for sample fixtures.

Files of interest
---------------
- `cv_text_extractor.py` — uses simple myumpdf + some heuristics to extract plain text from docx and pdf files.
- `cv_extraction_system_prompt.txt` — extensive explanation to LLM how to parse text and generate JSON document
- `llm_client.py` — abstracts access to LLM (single question + system prompt + user prompt, Ollama or OpenAPI)
- `cv_llm_converter.py` — CLI to run extraction + redaction and emit JSON.

License
-------
MIT-style (see repo settings).

Using OpenAI via `--llm-kind`
-----------------------------
You can use OpenAI as the LLM provider by selecting `--llm-kind openai`. The CLI will call the LLM client which in turn uses the `openai` Python package. Set your API key in the environment first:

```bash
export OPENAI_API_KEY="sk_your_key_here"
python3 cv_llm_converter.py resume.docx --llm --llm-kind openai --llm-model gpt-4o-mini -o out.json
```

If `OPENAI_API_KEY` is not set the OpenAI client will raise an error. The default LLM provider remains `ollama`.

If system tools are not available the CLI prints a warning and falls back to the default extraction path.