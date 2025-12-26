#!/usr/bin/env python3
import argparse
import json
import logging
import shutil
import subprocess
from typing import Tuple, Dict, Optional
from pathlib import Path

from extract_text import extract_text_structured
from redactor import redact_structured
from llm_client import create_llm_client
from jsonschema import validate as jsonschema_validate, ValidationError
from converter import convert_input
import time

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def _check_tool(name: str) -> Optional[str]:
    return shutil.which(name)


def _run_cmd(cmd: list, *, check: bool = True):
    return subprocess.run(cmd, check=check)


def main(argv=None):
    p = argparse.ArgumentParser(description="Extract structured text and redact PII with optional preprocessing")
    p.add_argument("path", help="Path to input file (PDF or DOCX)")
    p.add_argument("-o", "--out", help="Output JSON file (default stdout)")
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

    logger.info(f"Processing file: {src}")
    
    try:
        # delegate conversion / preprocessing to converter component
        logger.info("Converting/preprocessing input file...")
        conv = convert_input(str(src), pandoc_ast=args.pandoc_ast)
        work_path = conv.get("work_path", src)
        tmp_files = conv.get("tmp_files", [])
        pandoc_ast = conv.get("pandoc_ast")

        logger.info("Extracting structured text...")
        structured, diag = extract_text_structured(str(work_path))
        logger.info(f"Extracted {len(structured.get('pages', []))} page(s)")
        
        logger.info("Redacting PII...")
        redacted_struct, redactions = redact_structured(structured)
        logger.info(f"Applied {len(redactions)} redaction(s)")

        llm_response = None
        if args.llm:
            logger.info("Starting LLM processing...")
            logger.info(f"LLM kind: {args.llm_kind}, model: {args.llm_model}")
            
            spath = Path(args.system_prompt)
            schema_path = Path(args.schema)
            
            if not spath.exists():
                logger.warning(f"System prompt file not found: {spath}")
            else:
                logger.info(f"Using system prompt: {spath}")
                
            if not schema_path.exists():
                logger.warning(f"Schema file not found: {schema_path}")
            else:
                logger.info(f"Using schema: {schema_path}")
            
            try:
                logger.info("Creating LLM client...")
                client = create_llm_client(kind=args.llm_kind)
                logger.info(f"LLM client created: {type(client).__name__}")
                
                logger.info("Calling LLM to generate structured output...")
                llm_response = client.generate_structured(
                    redacted_struct,
                    system_prompt_path=args.system_prompt,
                    schema_path=args.schema,
                    model=args.llm_model,
                    max_length=2048,
                    max_retries=3,
                )
                logger.info("LLM processing completed successfully")
            except Exception as e:
                logger.error(f"LLM call failed: {e}", exc_info=True)
                llm_response = f"LLM call error: {e}"

        # If LLM was used, the LLM response is the primary output (converted CV JSON)
        # Otherwise, output the raw extracted structured data
        if llm_response is not None:
            try:
                # LLM response is the converted CV JSON - this is what we want as output
                llm_json = json.loads(llm_response)
                out = llm_json
                logger.info("Using LLM-generated JSON as primary output")
            except json.JSONDecodeError:
                # If LLM failed, fall back to raw data and include error
                logger.warning("LLM response is not valid JSON, using raw extracted data")
                out = {
                    "structured": redacted_struct,
                    "diagnostics": diag.__dict__ if hasattr(diag, "__dict__") else {},
                    "redactions": redactions,
                    "llm_error": llm_response,
                }
                if pandoc_ast is not None:
                    out["pandoc_ast"] = pandoc_ast
        else:
            # No LLM processing - output raw extracted data
            out = {
                "structured": redacted_struct,
                "diagnostics": diag.__dict__ if hasattr(diag, "__dict__") else {},
                "redactions": redactions,
            }
            if pandoc_ast is not None:
                out["pandoc_ast"] = pandoc_ast

        logger.info("Generating output...")
        data = json.dumps(out, ensure_ascii=False, indent=2)
        if args.out:
            logger.info(f"Writing output to: {args.out}")
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(data)
        else:
            print(data)

        if llm_response is not None:
            print('\n---- LLM response (also in output file) ----')
            print(llm_response)
            
        logger.info("Processing completed successfully")

    finally:
        # cleanup tmp files
        for t in tmp_files:
            try:
                Path(t).unlink()
            except Exception:
                pass


if __name__ == "__main__":
    main()
