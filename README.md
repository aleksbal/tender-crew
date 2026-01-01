# tender-crew

Small toolkit for extracting and redacting CV/resume text.

Usage
-----

1. Install runtime dependencies (examples):

```bash
pip install pymupdf python-docx
```

Quick smoke test with a DOCX

Install Ollama locally and connect with Ollama Cloud account

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

Run JSON extraction (writes JSON to stdout or use `-o`):

```bash
python3 text_2_json_cli.py sample_cv.docx --llm --llm-kind ollama --llm-model gpt-oss:120b-cloud -o sample_out.json
# Pretty-print result
python3 -m json.tool sample_out.json | less
```

Result:

```json
{
  "personal_info": {
    "first_name": "Max",
    "last_name": "Mustermann",
    "address": "Musterstraße 1, 12345 Musterstadt",
    "phone": "+49 123 4567890",
    "email": "max@example.com",
    "linkedin": "",
    "website": ""
  },
  "summary": "",
  "experience": [
    {
      "start_date": "2022-01",
      "end_date": "2023-02",
      "is_current": false,
      "company": "Company A",
      "role": "Developer",
      "location": "",
      "employment_type": "",
      "description": "- Working on a project with Java, MySQL and Gradle",
      "technologies": [
        "Java",
        "MySQL",
        "Gradle"
      ],
      "evidence": "01/2022 02/2023 | - Company A, Developer"
    },
    {
      "start_date": "2023-03",
      "end_date": "2025-12",
      "is_current": false,
      "company": "Company B",
      "role": "Developer",
      "location": "",
      "employment_type": "",
      "description": "- Working on a project with Python and PostgreSQL",
      "technologies": [
        "Python",
        "PostgreSQL"
      ],
      "evidence": "03/2023 12/2025 | - Company B, Developer"
    }
  ],
  "education": [],
  "skills": {
    "programming_languages": [],
    "technologies": [],
    "soft_skills": []
  },
  "projects": [],
  "certifications": [],
  "languages": []
}
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
python3 text_2_json_cli.py resume.docx --llm --llm-kind openai --llm-model gpt-4o-mini -o out.json
```

If `OPENAI_API_KEY` is not set the OpenAI client will raise an error. The default LLM provider remains `ollama`.

If system tools are not available the CLI prints a warning and falls back to the default extraction path.