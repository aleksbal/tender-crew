#!/usr/bin/env python3
import argparse
import json
import shutil
import subprocess
import tempfile
from typing import Tuple, Dict, Optional
from pathlib import Path

from extract_text import extract_text_structured
from redactor import redact_structured
from llm_client import create_llm_client
from jsonschema import validate as jsonschema_validate, ValidationError
import time


def _check_tool(name: str) -> Optional[str]:
    return shutil.which(name)


def _run_cmd(cmd: list, *, check: bool = True):
    return subprocess.run(cmd, check=check)


def main(argv=None):
    p = argparse.ArgumentParser(description="Extract structured text and redact PII with optional preprocessing")
    p.add_argument("path", help="Path to input file (PDF or DOCX)")
    p.add_argument("-o", "--out", help="Output JSON file (default stdout)")
    p.add_argument("--ocr", action="store_true", help="Run OCR (ocrmypdf) on input PDF before extraction")
    # NOTE: we avoid requiring LibreOffice by default; rendering DOCX to PDF
    # via soffice is intentionally omitted to keep the pipeline lightweight.
    p.add_argument("--pandoc-ast", action="store_true", help="Export pandoc JSON AST for DOCX (requires pandoc installed)")
    p.add_argument("--llm", action="store_true", help="Call local Ollama LLM to post-process structured output")
    p.add_argument("--llm-model", default="ollama/llama2", help="LLM model name for local Ollama")
    p.add_argument("--llm-kind", default="ollama", help="LLM provider kind (ollama|openai)")
    p.add_argument("--system-prompt", default="system_prompt.txt", help="Path to system prompt file")
    p.add_argument("--schema", default="schema.json", help="Path to JSON schema to present to LLM")
    args = p.parse_args(argv)

    src = Path(args.path)
    if not src.exists():
        raise SystemExit(f"Input not found: {src}")

    tmp_files = []
    pandoc_ast = None

    try:
        # Optional: produce pandoc AST for DOCX
        if args.pandoc_ast and src.suffix.lower() == ".docx":
            if not _check_tool("pandoc"):
                print("Warning: pandoc not found; --pandoc-ast skipped")
            else:
                ast_out = Path(tempfile.mkstemp(suffix=".json")[1])
                tmp_files.append(ast_out)
                _run_cmd(["pandoc", "--from", "docx", "--to", "json", "-o", str(ast_out), str(src)])
                try:
                    with open(ast_out, "r", encoding="utf-8") as fh:
                        pandoc_ast = json.load(fh)
                except Exception:
                    pandoc_ast = None

        work_path = src

        # If input is PDF and --ocr requested
        if args.ocr and src.suffix.lower() == ".pdf":
            if not _check_tool("ocrmypdf"):
                print("Warning: ocrmypdf not found; --ocr skipped")
            else:
                out_pdf = Path(tempfile.mkstemp(suffix=".pdf")[1])
                tmp_files.append(out_pdf)
                _run_cmd(["ocrmypdf", str(src), str(out_pdf)])
                work_path = out_pdf

        # We do not render DOCX via soffice by default (heavy runtime dependency).

        structured, diag = extract_text_structured(str(work_path))
        redacted_struct, redactions = redact_structured(structured)

        llm_response = None
        if args.llm:
            spath = Path(args.system_prompt)
            prompt = spath.read_text(encoding='utf-8') if spath.exists() else ''
            schema_text = Path(args.schema).read_text(encoding='utf-8') if Path(args.schema).exists() else ''

            # Build a compact prompt consisting of the system prompt, schema, and data
            composed = f"{prompt}\n\nSchema:\n{schema_text}\n\nData:\n{json.dumps(redacted_struct, ensure_ascii=False)}"
            try:
                client = create_llm_client(kind=args.llm_kind)
                llm_response = client.generate_structured(
                    redacted_struct,
                    system_prompt_path=args.system_prompt,
                    schema_path=args.schema,
                    model=args.llm_model,
                    max_length=2048,
                    max_retries=3,
                )
            except Exception as e:
                llm_response = f"LLM call error: {e}"

        out = {
            "structured": redacted_struct,
            "diagnostics": diag.__dict__ if hasattr(diag, "__dict__") else {},
            "redactions": redactions,
        }
        if pandoc_ast is not None:
            out["pandoc_ast"] = pandoc_ast

        data = json.dumps(out, ensure_ascii=False, indent=2)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(data)
        else:
            print(data)

        if llm_response is not None:
            print('\n---- LLM response ----')
            print(llm_response)

    finally:
        # cleanup tmp files
        for t in tmp_files:
            try:
                Path(t).unlink()
            except Exception:
                pass


if __name__ == "__main__":
    main()
